"""Dashboard API: FastAPI endpoints for all platform data.

Exposes live status, features, regimes, positions, validation runs,
metrics, and alerts via REST API.
"""

import os
import time
from pathlib import Path

import jwt as pyjwt
import httpx
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import Any

# ── JWT Auth: same secret as the bot, validates the bot's tokens ──
_JWT_SECRET = os.getenv("JWT_SECRET", "")
_BOT_API_URL = os.getenv("BOT_API_URL", "http://localhost:8180")


def _extract_user_id(request: Request) -> int | None:
    """Extract userId from JWT in Authorization header or cookie.
    Returns None if no valid token found."""
    auth = request.headers.get("Authorization", "")
    token = request.cookies.get("platform_token", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    if not token:
        return None
    try:
        secret = _JWT_SECRET
        if not secret:
            return None
        # Bot uses Keys.hmacShaKeyFor(secret.getBytes(UTF-8)) — raw bytes, not hex
        # Key length determines algorithm: >=64 bytes = HS512, >=48 = HS384, >=32 = HS256
        key_bytes = secret.encode("utf-8")
        payload = pyjwt.decode(token, key_bytes, algorithms=["HS256", "HS384", "HS512"])
        return int(payload.get("uid", 0)) or None
    except Exception:
        return None


def _require_user_id(request: Request) -> int:
    """Dependency: extract userId from JWT or raise 401."""
    uid = _extract_user_id(request)
    if uid is None:
        raise HTTPException(401, "Invalid or missing token")
    return uid


def _uid(request: Request) -> int:
    """Get userId from request state (set by auth middleware). Raises 401 if missing."""
    uid = getattr(request.state, "user_id", None)
    if uid is None:
        raise HTTPException(401, "User not authenticated")
    return uid


def create_app(
    feature_store=None,
    regime_detector=None,
    system_monitor=None,
    session_factory=None,
    audit_runner=None,
) -> FastAPI:
    app = FastAPI(title="AgentBot Platform", version="0.1.0")

    _allowed_origins = os.getenv("CORS_ORIGINS", "https://bot-v2.alchimiabot.com,https://platform-v2.alchimiabot.com,http://localhost:3001,http://localhost:8190").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    # Bot receiver API key (shared secret between bot and platform)
    _BOT_API_KEY = os.getenv("BOT_API_KEY", "")

    # Auth middleware — protect all /api/ endpoints
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or path == "/api/platform/login":
            return await call_next(request)

        # Bot data endpoints: accept API key (from bot backend) OR JWT (from dashboard frontend)
        if path.startswith("/api/bot/"):
            key = request.headers.get("X-Bot-Api-Key", "")
            uid = _extract_user_id(request)
            if _BOT_API_KEY and key == _BOT_API_KEY:
                return await call_next(request)  # bot backend with valid key
            if uid is not None:
                request.state.user_id = uid
                return await call_next(request)  # dashboard frontend with valid JWT
            if not _BOT_API_KEY:
                return await call_next(request)  # no key configured, allow all
            return JSONResponse(status_code=403, content={"error": "Invalid bot API key"})

        # Dashboard endpoints: validate JWT
        uid = _extract_user_id(request)
        if uid is None:
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        request.state.user_id = uid
        return await call_next(request)

    # Login endpoint — proxies to bot API for credential validation
    @app.post("/api/platform/login")
    async def platform_login(body: dict):
        username = body.get("username", "")
        password = body.get("password", "")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{_BOT_API_URL}/api/auth/login",
                    json={"username": username, "password": password},
                )
            if resp.status_code != 200:
                raise HTTPException(401, "Invalid credentials")
            data = resp.json()
            token = data.get("token", "")
            if not token:
                raise HTTPException(401, "Invalid credentials")
            # Return the bot's JWT — platform validates it with the same secret
            response = JSONResponse({"token": token, "username": username})
            response.set_cookie("platform_token", token, max_age=3600, httponly=False, samesite="lax")
            return response
        except httpx.RequestError:
            raise HTTPException(502, "Bot API unreachable")

    @app.get("/api/platform/me")
    async def platform_me(request: Request) -> dict:
        uid = _extract_user_id(request)
        if uid is None:
            raise HTTPException(401, "Unauthorized")
        return {"user_id": uid, "username": "user"}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        result: dict[str, Any] = {"status": "running"}
        if feature_store:
            result["features"] = feature_store.stats
        return result

    @app.get("/api/features/{coin}")
    def get_features(coin: str) -> dict[str, Any]:
        if not feature_store:
            raise HTTPException(503, "Feature store not available")
        snap = feature_store.get_snapshot(coin.upper())
        return snap.to_dict()

    @app.get("/api/features")
    def get_all_features() -> dict[str, Any]:
        if not feature_store:
            raise HTTPException(503, "Feature store not available")
        snaps = feature_store.get_all_snapshots()
        return {coin: snap.to_dict() for coin, snap in snaps.items()}

    @app.get("/api/regime/{coin}")
    def get_regime(coin: str) -> dict[str, Any]:
        if not regime_detector:
            raise HTTPException(503, "Regime detector not available")
        state = regime_detector.detect(coin.upper())
        return state.to_dict()

    @app.get("/api/regimes")
    async def get_all_regimes() -> dict[str, Any]:
        # Preferred path: live regime_detector in process memory.
        if regime_detector:
            coins = feature_store.tracked_coins if feature_store else []
            states = regime_detector.detect_all(coins)
            return {coin: state.to_dict() for coin, state in states.items()}

        # Fallback: the bot's HlTrendService pushes a regime label per coin
        # every 60s to regime_labels. Read the most recent row per coin so the
        # Regimes tab has fresh data even when the local detector isn't wired.
        if not session_factory:
            return {}
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(text(
                "SELECT DISTINCT ON (coin) coin, regime, confidence, "
                "trend_strength, volatility_level "
                "FROM regime_labels "
                "WHERE timestamp > now() - interval '10 minutes' "
                "ORDER BY coin, timestamp DESC"))
            out: dict[str, Any] = {}
            for row in result.mappings().all():
                out[row["coin"]] = {
                    "regime": row["regime"],
                    "confidence": row["confidence"],
                    "trend_strength": row["trend_strength"],
                    "volatility_level": row["volatility_level"],
                }
            return out

    # NOTE: /api/positions, /api/policy, /api/risk, /api/metrics, /api/alerts
    # were removed 2026-04-23 — their collaborators (position_manager,
    # policy_engine, risk_manager, metrics_collector, alert_manager) were
    # never wired into PlatformRunner, so every call returned an empty dict
    # or list. Dashboard positions come from /api/bot/* (bot receiver) and
    # metrics from the dedicated endpoints under /api/system/*.

    # ── System Health ──

    @app.get("/api/system/disk")
    def get_disk() -> dict[str, Any]:
        if not system_monitor:
            return {"error": "System monitor not available"}
        return system_monitor.get_disk_status()

    @app.get("/api/system/disk/alerts")
    def get_disk_alerts() -> list[dict]:
        if not system_monitor:
            return []
        return system_monitor.check_alerts()

    # ── Bot Integration ──

    if session_factory:
        from ..ingestion.rest.bot_receiver import router as bot_router, set_session_factory
        set_session_factory(session_factory)
        app.include_router(bot_router)

    # ── Bot Live Data ──

    @app.get("/api/bot/trades")
    async def get_bot_trades(request: Request) -> list[dict]:
        if not session_factory:
            return []
        uid = _uid(request)
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM trade_outcomes WHERE user_id = :uid ORDER BY exit_time DESC LIMIT 100"),
                {"uid": uid})
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    @app.get("/api/bot/trades/stats")
    async def get_bot_trade_stats(request: Request) -> dict:
        if not session_factory:
            return {}
        uid = _uid(request)
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(text("""
                SELECT
                    count(*) as total_trades,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    sum(case when net_pnl <= 0 then 1 else 0 end) as losses,
                    round(sum(gross_pnl)::numeric, 4) as total_gross,
                    round(sum(fee)::numeric, 4) as total_fees,
                    round(sum(net_pnl)::numeric, 4) as total_net,
                    round(avg(case when net_pnl > 0 then net_pnl end)::numeric, 4) as avg_win,
                    round(avg(case when net_pnl <= 0 then net_pnl end)::numeric, 4) as avg_loss,
                    round(avg(hold_seconds)::numeric, 0) as avg_hold
                FROM trade_outcomes WHERE user_id = :uid
            """), {"uid": uid})
            row = result.mappings().first()
            if not row or row["total_trades"] == 0:
                return {"total_trades": 0}
            d = dict(row)
            total = d["total_trades"]
            d["win_rate"] = round(float(d["wins"] or 0) / total, 4) if total else 0
            return d

    @app.get("/api/bot/signals")
    async def get_bot_signals(request: Request) -> list[dict]:
        if not session_factory:
            return []
        uid = _uid(request)
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM signal_evaluations WHERE user_id = :uid ORDER BY timestamp DESC LIMIT 200"),
                {"uid": uid})
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    @app.get("/api/bot/signals/stats")
    async def get_bot_signal_stats(request: Request) -> dict:
        if not session_factory:
            return {}
        uid = _uid(request)
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(text("""
                SELECT action, count(*) as total,
                       round(avg(signal_score)::numeric, 4) as avg_score
                FROM signal_evaluations WHERE user_id = :uid
                GROUP BY action ORDER BY total DESC
            """), {"uid": uid})
            rows = result.mappings().all()
            return {"actions": [dict(r) for r in rows]}

    @app.get("/api/bot/regimes")
    async def get_bot_regimes(request: Request) -> list[dict]:
        if not session_factory:
            return []
        uid = _uid(request)
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(text("""
                SELECT coin, regime, confidence, trend_strength, volatility_level, timestamp
                FROM regime_labels
                WHERE user_id = :uid AND timestamp > now() - interval '1 hour'
                ORDER BY timestamp DESC
            """), {"uid": uid})
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    # ── Feature Contract ──

    @app.get("/api/feature-contract")
    def get_feature_contract() -> dict[str, Any]:
        from ..features.contract import get_contract_summary
        return get_contract_summary()

    @app.get("/api/feature-contract/validate/{coin}")
    def validate_features(coin: str) -> dict[str, Any]:
        if not feature_store:
            raise HTTPException(503, "Feature store not available")
        from ..features.contract import validate_snapshot
        snap = feature_store.get_snapshot(coin.upper())
        errors = validate_snapshot(snap.features)
        return {"coin": coin, "valid": len(errors) == 0, "errors": errors}

    # NOTE: /api/research/queries removed 2026-04-23 — dashboard never called
    # it. The underlying `src/research/queries.py` module is still used for
    # offline analysis scripts and stays as-is.

    # ── Quant Layer ──
    if session_factory:
        from ..quant.datasets.trades_enriched import TradesEnrichedBuilder
        from ..quant.metrics.engine import MetricsEngine
        from ..quant.experiments.engine import ExperimentEngine, ExperimentConfig
        from ..quant.analysis.engine import AnalysisEngine
        from ..quant.analysis.feature_importance import FeatureImportanceAnalyzer
        from ..quant.analysis.counterfactual import CounterfactualAnalyzer
        from ..quant.decision.engine import DecisionEngine

        _enriched_builder = TradesEnrichedBuilder(session_factory)
        _metrics_engine = MetricsEngine()
        _experiment_engine = ExperimentEngine()
        _analysis_engine = AnalysisEngine()
        _feature_analyzer = FeatureImportanceAnalyzer()
        _counterfactual_analyzer = CounterfactualAnalyzer()
        _decision_engine = DecisionEngine()

        @app.get("/api/quant/dataset")
        async def quant_dataset(request: Request, date_from: str | None = None) -> dict:
            data = await _enriched_builder.build_with_signals(date_from=date_from, user_id=_uid(request))
            return {"trades": len(data["trades"]), "signals": data["signals"]}

        @app.get("/api/quant/metrics")
        async def quant_metrics(request: Request, date_from: str | None = None) -> dict:
            trades = await _enriched_builder.build(date_from=date_from, user_id=_uid(request))
            return _metrics_engine.compute(trades)

        @app.post("/api/quant/experiment")
        async def quant_experiment(config: dict, request: Request) -> dict:
            trades = await _enriched_builder.build(user_id=_uid(request))
            exp_config = ExperimentConfig(**config)
            result = _experiment_engine.run(trades, exp_config)
            return result.to_dict()

        @app.get("/api/quant/analysis")
        async def quant_analysis(request: Request, date_from: str | None = None) -> dict:
            data = await _enriched_builder.build_with_signals(date_from=date_from, user_id=_uid(request))
            return _analysis_engine.analyze(data["trades"], data["signals"])

        @app.get("/api/quant/feature-importance")
        async def quant_feature_importance(request: Request) -> dict:
            trades = await _enriched_builder.build(user_id=_uid(request))
            return _feature_analyzer.analyze(trades)

        @app.get("/api/quant/decisions")
        async def quant_decisions(request: Request, date_from: str | None = None) -> list[dict]:
            data = await _enriched_builder.build_with_signals(date_from=date_from, user_id=_uid(request))
            trades = data["trades"]
            metrics = _metrics_engine.compute(trades)
            analysis = _analysis_engine.analyze(trades, data["signals"])
            decisions = _decision_engine.generate(metrics, analysis, trades)
            return [d.to_dict() for d in decisions]

        @app.get("/api/quant/full")
        async def quant_full(request: Request, date_from: str | None = None) -> dict:
            """Complete quant report: metrics + analysis + decisions in one call."""
            data = await _enriched_builder.build_with_signals(date_from=date_from, user_id=_uid(request))
            trades = data["trades"]
            metrics = _metrics_engine.compute(trades)
            analysis = _analysis_engine.analyze(trades, data["signals"])
            decisions = _decision_engine.generate(metrics, analysis, trades)
            return {
                "total_trades": len(trades),
                "signals": data["signals"],
                "metrics": metrics,
                "analysis": analysis,
                "decisions": [d.to_dict() for d in decisions],
            }

        @app.get("/api/quant/entry-quality")
        async def quant_entry_quality(request: Request, date_from: str | None = None) -> dict:
            from ..quant.analysis.entry_quality import EntryQualityAnalyzer
            trades = await _enriched_builder.build(date_from=date_from, user_id=_uid(request))
            analyzer = EntryQualityAnalyzer()
            return analyzer.analyze(trades)

        @app.get("/api/quant/config-analysis")
        async def quant_config_analysis(request: Request, date_from: str | None = None) -> dict:
            from ..quant.analysis.config_analysis import ConfigAnalyzer
            trades = await _enriched_builder.build(date_from=date_from, user_id=_uid(request))
            analyzer = ConfigAnalyzer()
            return analyzer.analyze(trades)

        @app.get("/api/quant/counterfactual")
        async def quant_counterfactual(request: Request, date_from: str | None = None) -> dict:
            """Counterfactual analysis: what-if at different score thresholds."""
            uid = _uid(request)
            from sqlalchemy import text as sql_text
            from ..quant.datasets.trades_enriched import _coerce_date
            df = _coerce_date(date_from)
            signals = []
            async with session_factory() as s:
                where_parts = ["user_id = :uid"]
                params: dict = {"uid": uid}
                if df is not None:
                    where_parts.append("timestamp >= :date_from")
                    params["date_from"] = df
                where = "WHERE " + " AND ".join(where_parts)
                result = await s.execute(sql_text(f"""
                    SELECT coin, side, signal_score, action, reason, mode,
                           score_min_applied, config_version
                    FROM signal_evaluations {where}
                    ORDER BY timestamp DESC LIMIT 5000
                """), params)
                signals = [dict(r) for r in result.mappings().all()]
            return _counterfactual_analyzer.analyze(signals)

        @app.get("/api/quant/diagnostic")
        async def quant_diagnostic(request: Request, date_from: str | None = None) -> dict:
            """Diagnostic trace analysis: evaluate ALL filters pass rates."""
            uid = _uid(request)
            from sqlalchemy import text as sql_text
            from ..quant.datasets.trades_enriched import _coerce_date
            df = _coerce_date(date_from)
            async with session_factory() as s:
                where_parts = ["diagnostic_trace IS NOT NULL", "user_id = :uid"]
                params: dict = {"uid": uid}
                if df is not None:
                    where_parts.append("timestamp >= :d")
                    params["d"] = df
                where = "WHERE " + " AND ".join(where_parts)

                result = await s.execute(sql_text(f"""
                    SELECT diagnostic_trace FROM signal_evaluations
                    {where}
                    ORDER BY timestamp DESC LIMIT 2000
                """), params)
                traces = [r["diagnostic_trace"] for r in result.mappings().all() if r["diagnostic_trace"]]

            if not traces:
                return {"status": "no_data", "message": "No diagnostic traces yet"}

            # Calculate pass rates
            fields = ["score_pass", "momentum_2m_pass", "confirmation_1m_pass", "entry_threshold_pass",
                       "trend_1h_pass", "trend_4h_pass", "btc_macro_pass", "rsi_pass", "micro_pass"]

            pass_rates = {}
            for field in fields:
                passed = sum(1 for t in traces if t.get(field) is True)
                pass_rates[field] = {"passed": passed, "total": len(traces), "rate": round(passed / len(traces) * 100, 1)}

            # Near perfect: score fails but everything else passes
            near_perfect = sum(1 for t in traces if not t.get("score_pass")
                               and t.get("momentum_2m_pass") and t.get("confirmation_1m_pass")
                               and t.get("trend_1h_pass") and t.get("btc_macro_pass") and t.get("rsi_pass"))

            # Score passes but blocked by filters
            score_ok_blocked = sum(1 for t in traces if t.get("score_pass")
                                   and (not t.get("trend_1h_pass") or not t.get("btc_macro_pass")
                                        or not t.get("rsi_pass") or not t.get("micro_pass")))

            # Bottleneck ranking
            bottleneck = sorted(pass_rates.items(), key=lambda x: x[1]["rate"])

            return {
                "total_signals": len(traces),
                "pass_rates": pass_rates,
                "near_perfect_blocked_by_score": near_perfect,
                "score_ok_but_filtered": score_ok_blocked,
                "bottleneck_ranking": [{"filter": k, "pass_rate": v["rate"]} for k, v in bottleneck],
            }

        @app.get("/api/quant/rejections")
        async def quant_rejections(request: Request, date_from: str | None = None) -> dict:
            """Rejection breakdown from signal evaluations with decision_stage."""
            uid = _uid(request)
            from sqlalchemy import text as sql_text
            params: dict = {"uid": uid}
            where_parts = ["action = 'BLOCKED'", "user_id = :uid"]
            if date_from:
                where_parts.append("timestamp >= :date_from")
                params["date_from"] = date_from
            where = "WHERE " + " AND ".join(where_parts)

            async with session_factory() as s:
                # By reason + decision_stage
                result = await s.execute(sql_text(f"""
                    SELECT reason, decision_stage, count(*) as cnt,
                           round(avg(signal_score)::numeric, 2) as avg_score,
                           round(avg(score_min_applied)::numeric, 2) as avg_score_min,
                           mode
                    FROM signal_evaluations {where}
                    GROUP BY reason, decision_stage, mode ORDER BY cnt DESC
                """), params)
                by_reason = [dict(r) for r in result.mappings().all()]

                # Totals by action
                totals_where = "WHERE user_id = :uid" + (" AND timestamp >= :date_from" if date_from else "")
                result2 = await s.execute(sql_text(f"""
                    SELECT action, count(*) as cnt,
                           round(avg(signal_score)::numeric, 2) as avg_score
                    FROM signal_evaluations {totals_where}
                    GROUP BY action
                """), params)
                totals = {r["action"]: dict(r) for r in result2.mappings().all()}

                # Stage breakdown
                result3 = await s.execute(sql_text(f"""
                    SELECT decision_stage, count(*) as cnt,
                           round(avg(signal_score)::numeric, 2) as avg_score
                    FROM signal_evaluations {totals_where}
                    GROUP BY decision_stage
                """), params)
                by_stage = {(r["decision_stage"] or "LEGACY"): dict(r) for r in result3.mappings().all()}

                # Near misses: PRE_CANDIDATE_REJECT where score was within 5 of threshold
                nm_where_parts = ["decision_stage = 'PRE_CANDIDATE_REJECT'", "user_id = :uid"]
                if date_from:
                    nm_where_parts.append("timestamp >= :date_from")
                nm_where = "WHERE " + " AND ".join(nm_where_parts)
                result4 = await s.execute(sql_text(f"""
                    SELECT coin, side, reason, signal_score, score_min_applied, mode,
                           decision_trace, timestamp
                    FROM signal_evaluations {nm_where}
                    AND signal_score >= score_min_applied - 5
                    ORDER BY timestamp DESC LIMIT 50
                """), params)
                near_misses = [dict(r) for r in result4.mappings().all()]

            return {"by_reason": by_reason, "totals": totals,
                    "by_stage": by_stage, "near_misses": near_misses}

    # ── Trade Detail, Verdicts & Cleanup ──
    if session_factory:
        from ..quant.analysis.trade_analyzer import TradeAnalyzer
        _trade_analyzer = TradeAnalyzer()

        @app.delete("/api/trades/snapshots/cleanup")
        async def cleanup_old_snapshots(request: Request, days: int = 7) -> dict:
            """Delete snapshots older than N days to save disk."""
            if days < 1:
                raise HTTPException(400, "days must be >= 1")
            from sqlalchemy import text as sql_text
            async with session_factory() as session:
                result = await session.execute(sql_text(
                    "DELETE FROM trade_snapshots WHERE user_id = :uid AND timestamp < NOW() - :days * interval '1 day' RETURNING id"
                ), {"uid": _uid(request), "days": days})
                deleted = len(result.fetchall())
                await session.commit()
                return {"deleted": deleted, "days": days}

        @app.get("/api/trades/snapshots/stats")
        async def snapshots_stats(request: Request) -> dict:
            """How much disk are snapshots using (per user)."""
            uid = _uid(request)
            from sqlalchemy import text as sql_text
            async with session_factory() as session:
                r = await session.execute(sql_text("""
                    SELECT count(*) as total,
                           min(timestamp) as oldest,
                           max(timestamp) as newest
                    FROM trade_snapshots WHERE user_id = :uid
                """), {"uid": uid})
                row = r.mappings().first()
                return dict(row) if row else {}

        @app.get("/api/trades")
        async def list_trades(request: Request, limit: int = 50, offset: int = 0) -> list[dict]:
            """List trades with verdict info, paginated, per user."""
            uid = _uid(request)
            from sqlalchemy import text as sql_text
            async with session_factory() as session:
                result = await session.execute(sql_text("""
                    SELECT t.*,
                           v.verdict, v.verdict_reason, v.entry_timing,
                           v.mfe_capture_pct AS verdict_mfe_capture,
                           v.fee_killed, v.improvements,
                           v.time_in_profit_pct AS verdict_time_in_profit
                    FROM trade_outcomes t
                    LEFT JOIN trade_verdicts v ON v.trade_outcome_id = t.id
                    WHERE t.user_id = :uid
                    ORDER BY t.entry_time DESC
                    LIMIT :limit OFFSET :offset
                """), {"uid": uid, "limit": limit, "offset": offset})
                rows = result.mappings().all()
                return [dict(r) for r in rows]

        @app.get("/api/trades/{trade_id}")
        async def get_trade_detail(trade_id: int, request: Request):
            """Full trade detail with snapshots, signal, verdict."""
            from sqlalchemy import text as sql_text
            from fastapi.encoders import jsonable_encoder
            async with session_factory() as session:
                # 1. Get trade outcome (scoped by user_id)
                uid = _uid(request)
                r = await session.execute(
                    sql_text("SELECT * FROM trade_outcomes WHERE id = :id AND user_id = :uid"),
                    {"id": trade_id, "uid": uid},
                )
                trade_row = r.mappings().first()
                if not trade_row:
                    raise HTTPException(404, f"Trade {trade_id} not found")
                trade = dict(trade_row)

                # 2. Get matching signal evaluation (same coin, side, closest to entry_time)
                signal = None
                if trade.get("entry_time"):
                    from datetime import timedelta
                    et = trade["entry_time"]
                    r2 = await session.execute(sql_text("""
                        SELECT * FROM signal_evaluations
                        WHERE user_id = :uid AND coin = :coin AND side = :side
                          AND action = 'ENTER'
                          AND timestamp BETWEEN :t_from AND :t_to
                        ORDER BY ABS(EXTRACT(EPOCH FROM (timestamp - :entry_time)))
                        LIMIT 1
                    """), {
                        "uid": uid,
                        "coin": trade["coin"],
                        "side": trade["side"],
                        "entry_time": et,
                        "t_from": et - timedelta(minutes=5),
                        "t_to": et + timedelta(minutes=1),
                    })
                    sig_row = r2.mappings().first()
                    if sig_row:
                        signal = dict(sig_row)

                # 3. Get snapshots — prefer trade_id match, fallback to coin+side+time
                snapshots = []
                if trade.get("entry_time") and trade.get("exit_time"):
                    r3 = await session.execute(sql_text("""
                        SELECT * FROM trade_snapshots
                        WHERE user_id = :uid AND coin = :coin AND side = :side
                          AND trade_id = :trade_id
                          AND timestamp BETWEEN :entry_time AND :exit_time
                        ORDER BY timestamp
                    """), {
                        "uid": uid,
                        "trade_id": str(trade_id),
                        "coin": trade["coin"],
                        "side": trade["side"],
                        "entry_time": trade["entry_time"],
                        "exit_time": trade["exit_time"],
                    })
                    snapshots = [dict(row) for row in r3.mappings().all()]

                # 4. Get existing verdict
                r4 = await session.execute(
                    sql_text("SELECT * FROM trade_verdicts WHERE trade_outcome_id = :id"),
                    {"id": trade_id},
                )
                verdict_row = r4.mappings().first()

                # 5. If no verdict, run analyzer and save
                if not verdict_row:
                    import json as _json
                    verdict_data = await _trade_analyzer.analyze(trade, signal, snapshots)
                    # Serialize JSONB fields for PostgreSQL
                    if isinstance(verdict_data.get("improvements"), (list, dict)):
                        verdict_data["improvements"] = _json.dumps(verdict_data["improvements"])
                    if isinstance(verdict_data.get("counterfactual"), (list, dict)):
                        verdict_data["counterfactual"] = _json.dumps(verdict_data["counterfactual"])
                    # Insert verdict
                    # Add user_id from the trade
                    verdict_data["user_id"] = trade.get("user_id", 1)
                    cols = [
                        "user_id", "trade_outcome_id", "coin", "side", "mode",
                        "entry_time", "exit_time",
                        "verdict", "verdict_reason",
                        "entry_score", "entry_quality", "entry_timing", "trend_aligned",
                        "mfe_pct", "mae_pct", "mfe_capture_pct", "time_in_profit_pct",
                        "sl_moved", "sl_moves_count",
                        "max_sl_distance_pct", "min_sl_distance_pct",
                        "gross_pnl", "fee", "net_pnl", "fee_killed", "exit_reason",
                        "improvements", "counterfactual",
                    ]
                    placeholders = ", ".join(f":{c}" for c in cols)
                    col_names = ", ".join(cols)
                    await session.execute(
                        sql_text(f"INSERT INTO trade_verdicts ({col_names}) VALUES ({placeholders})"),
                        verdict_data,
                    )
                    await session.commit()
                    verdict = verdict_data
                else:
                    verdict = dict(verdict_row)

                return jsonable_encoder({
                    "trade": trade,
                    "signal": signal,
                    "snapshots": snapshots,
                    "verdict": verdict,
                })

    # ── Executive Summary & Score Parity ──
    if session_factory:
        from ..quant.executive_summary import ExecutiveSummaryBuilder
        from ..quant.analysis.score_parity import ScoreParityAnalyzer

        _summary_builder = ExecutiveSummaryBuilder(session_factory)
        _score_parity_analyzer = ScoreParityAnalyzer(session_factory)

        @app.get("/api/quant/executive-summary")
        async def executive_summary(request: Request) -> dict:
            return await _summary_builder.build(user_id=_uid(request))

        @app.get("/api/quant/score-parity")
        async def score_parity(request: Request) -> dict:
            return await _score_parity_analyzer.analyze(user_id=_uid(request))

    # ── Operational Reports ──
    # ── Validation Runner ──
    if session_factory:
        @app.get("/api/quant/validation")
        async def quant_validation(request: Request, date_from: str | None = None, date_to: str | None = None,
                                   mode: str = "live_trades", coin: str | None = None) -> dict:
            from ..quant.validation.runner import ValidationRunner
            runner = ValidationRunner()
            if mode == "replay_historical":
                coins = [coin] if coin else None
                report = runner.run_full_validation([], mode="replay_historical",
                                                     date_from=date_from, date_to=date_to, coins=coins)
            else:
                trades = await _enriched_builder.build(date_from=date_from, user_id=_uid(request))
                report = runner.run_full_validation(trades, mode="live_trades")
            return report.to_dict()

        @app.get("/api/quant/validation/{batch_name}")
        async def quant_validation_batch(request: Request, batch_name: str, date_from: str | None = None) -> dict:
            from ..quant.validation.runner import ValidationRunner
            trades = await _enriched_builder.build(date_from=date_from, user_id=_uid(request))
            runner = ValidationRunner()
            result = runner.run_single_batch(trades, batch_name)
            return result.to_dict()

    # ── Operational Reports ──
    if session_factory:
        from ..research.operational_reports import OperationalReports
        reports = OperationalReports(session_factory)

        @app.get("/api/reports/full")
        async def full_report(request: Request) -> dict:
            return await reports.full_report(user_id=_uid(request))

        _ALLOWED_REPORTS = {
            "wr_by_coin", "wr_by_side", "wr_by_hour", "pnl_by_mode", "pnl_by_tag",
            "pnl_by_exit_reason", "fee_analysis", "poison_coins", "rescuable_coins",
            "signal_blocked_vs_entered", "daily_summary",
        }

        @app.get("/api/reports/{name}")
        async def get_report(request: Request, name: str) -> Any:
            if name not in _ALLOWED_REPORTS:
                raise HTTPException(404, f"Report '{name}' not found")
            method = getattr(reports, name, None)
            if not method:
                raise HTTPException(404, f"Report '{name}' not found")
            return await method(user_id=_uid(request))

    # ── Audit System ──
    if audit_runner:
        @app.get("/api/audit/status")
        def audit_status() -> dict:
            return audit_runner.status

        @app.get("/api/audit/findings")
        def audit_findings() -> list[dict]:
            return audit_runner.findings

        @app.get("/api/audit/run")
        async def run_all_audits() -> dict:
            return await audit_runner.run_all_now()

        @app.get("/api/audit/history")
        async def audit_history() -> list[dict]:
            if not session_factory:
                return []
            from sqlalchemy import text
            async with session_factory() as s:
                r = await s.execute(text("""
                    SELECT id, audit_type, status, score, started_at, finished_at, summary, metrics
                    FROM audit_runs ORDER BY started_at DESC LIMIT 100"""))
                return [dict(row) for row in r.mappings().all()]

    # ── Daily Report ──
    if session_factory:
        from ..audit.daily_report import DailyReportGenerator
        daily_gen = DailyReportGenerator(session_factory)

        @app.get("/api/daily-report")
        async def get_daily_report(request: Request) -> dict:
            return await daily_gen.generate(user_id=_uid(request))

        @app.get("/api/daily-report/{date_str}")
        async def get_daily_report_date(request: Request, date_str: str) -> dict:
            from datetime import date as d
            report_date = d.fromisoformat(date_str)
            return await daily_gen.generate(report_date, user_id=_uid(request))

        # NOTE: /api/daily-report/history/list removed 2026-04-23 — the UI
        # fetches specific dates via /api/daily-report/{date_str}, never the
        # full history list.

    # ── Change Markers ──
    if session_factory:
        from ..quant.markers.marker_service import MarkerService
        _marker_service = MarkerService(session_factory)

        @app.get("/api/markers")
        async def list_markers(request: Request, limit: int = 50, days: int = 90) -> list[dict]:
            from fastapi.encoders import jsonable_encoder
            return jsonable_encoder(await _marker_service.get_markers(limit=limit, days=days, user_id=_uid(request)))

        @app.get("/api/markers/recent-impacts")
        async def recent_impacts(request: Request, limit: int = 10) -> list[dict]:
            from fastapi.encoders import jsonable_encoder
            return jsonable_encoder(await _marker_service.get_recent_with_impact(limit=limit, user_id=_uid(request)))

        @app.get("/api/markers/{marker_id}")
        async def get_marker_detail(request: Request, marker_id: int) -> dict:
            from fastapi.encoders import jsonable_encoder
            m = await _marker_service.get_marker(marker_id, user_id=_uid(request))
            if not m:
                raise HTTPException(404, "Marker not found")
            return jsonable_encoder(m)

        @app.post("/api/markers/{marker_id}/recalculate")
        async def recalculate_marker(request: Request, marker_id: int) -> dict:
            return await _marker_service.calculate_impact(marker_id, force=True, user_id=_uid(request))

        @app.post("/api/markers")
        async def create_marker_manual(payload: dict) -> dict:
            marker_id = await _marker_service.create_marker(**payload)
            return {"id": marker_id, "ok": True}

    # ── Bot Gate Stats (V25) ──
    # Live telemetry pushed by the bot showing which entry filters are
    # rejecting signals. Used by the dashboard "Gate Stats" panel to
    # surface invisible bottlenecks like the SL viability filter.
    @app.get("/api/bot/gate-stats")
    async def dashboard_gate_stats() -> dict:
        from ..ingestion.rest.bot_receiver import get_latest_gate_stats
        return get_latest_gate_stats()

    # ── Engine Shadow Mode ──
    # Proxies admin endpoints on the bot (/api/admin/shadow/*). Platform
    # does not store variants nor evaluations itself — the bot owns both.
    # Every call forwards the caller's JWT so the bot's own admin gate
    # applies (Role.ADMIN only).

    def _extract_bot_token(request: Request) -> str | None:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        tok = request.cookies.get("platform_token", "")
        return tok or None

    async def _shadow_proxy(method: str, path: str, request: Request, body: dict | None = None):
        token = _extract_bot_token(request)
        if not token:
            raise HTTPException(401, "No token to forward to bot")
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                if method == "GET":
                    resp = await client.get(
                        f"{_BOT_API_URL}{path}",
                        headers=headers,
                        params=dict(request.query_params),
                    )
                elif method == "POST":
                    resp = await client.post(
                        f"{_BOT_API_URL}{path}",
                        headers=headers,
                        json=body or {},
                    )
                elif method == "PUT":
                    resp = await client.put(
                        f"{_BOT_API_URL}{path}",
                        headers=headers,
                        json=body or {},
                    )
                elif method == "DELETE":
                    resp = await client.delete(
                        f"{_BOT_API_URL}{path}",
                        headers=headers,
                    )
                else:
                    raise HTTPException(500, f"Unsupported method {method}")
        except httpx.RequestError as exc:
            raise HTTPException(502, f"Bot API unreachable: {exc}")
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise HTTPException(resp.status_code, detail)
        return resp.json()

    @app.get("/api/shadow/variants")
    async def shadow_list_variants(request: Request):
        return await _shadow_proxy("GET", "/api/admin/shadow/variants", request)

    @app.post("/api/shadow/variants")
    async def shadow_register_variant(body: dict, request: Request):
        return await _shadow_proxy("POST", "/api/admin/shadow/variants", request, body=body)

    @app.delete("/api/shadow/variants/{name}")
    async def shadow_unregister_variant(name: str, request: Request):
        return await _shadow_proxy("DELETE", f"/api/admin/shadow/variants/{name}", request)

    @app.get("/api/shadow/variants/{name}/associated")
    async def shadow_variant_associated(name: str, request: Request):
        return await _shadow_proxy("GET", f"/api/admin/shadow/variants/{name}/associated", request)

    @app.put("/api/shadow/variants/{name}")
    async def shadow_update_variant(name: str, body: dict, request: Request):
        return await _shadow_proxy("PUT", f"/api/admin/shadow/variants/{name}", request, body=body)

    @app.get("/api/shadow/evaluations")
    async def shadow_evaluations(request: Request):
        return await _shadow_proxy("GET", "/api/admin/shadow/evaluations", request)

    @app.get("/api/shadow/summary")
    async def shadow_summary(request: Request):
        return await _shadow_proxy("GET", "/api/admin/shadow/summary", request)

    @app.get("/api/shadow/exits")
    async def shadow_exits(request: Request):
        # Forward query string (variant, limit) so the bot filters identically.
        qs = request.url.query
        suffix = f"?{qs}" if qs else ""
        return await _shadow_proxy("GET", f"/api/admin/shadow/exits{suffix}", request)

    @app.get("/api/shadow/exits/summary")
    async def shadow_exits_summary(request: Request):
        qs = request.url.query
        suffix = f"?{qs}" if qs else ""
        return await _shadow_proxy("GET", f"/api/admin/shadow/exits/summary{suffix}", request)

    # ── Serve frontend ──
    _static = Path(os.path.dirname(os.path.abspath(__file__))) / "static"
    if _static.exists():
        app.mount("/static", StaticFiles(directory=str(_static)), name="static")

        @app.get("/", response_class=FileResponse)
        async def serve_index():
            return FileResponse(str(_static / "index.html"), media_type="text/html")

    return app
