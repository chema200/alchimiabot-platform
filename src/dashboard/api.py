"""Dashboard API: FastAPI endpoints for all platform data.

Exposes live status, features, regimes, positions, experiments,
metrics, and alerts via REST API.
"""

import os
import hashlib
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import Any

# Simple JWT-like token auth for platform dashboard
_PLATFORM_USER = "chema200"
_PLATFORM_PASS_HASH = hashlib.sha256("iotron4321".encode()).hexdigest()
_tokens: dict[str, float] = {}  # token -> expiry timestamp
_TOKEN_TTL = 3600  # 1 hour


def _create_token() -> str:
    token = secrets.token_hex(32)
    _tokens[token] = time.time() + _TOKEN_TTL
    return token


def _verify_token(request: Request) -> bool:
    # Skip auth for login endpoint and static files
    path = request.url.path
    if path in ("/api/platform/login", "/", "/static") or path.startswith("/static"):
        return True
    auth = request.headers.get("Authorization", "")
    token = request.cookies.get("platform_token", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    expiry = _tokens.get(token, 0)
    if expiry > time.time():
        return True
    return False


def create_app(
    feature_store=None,
    regime_detector=None,
    position_manager=None,
    policy_engine=None,
    risk_manager=None,
    metrics_collector=None,
    alert_manager=None,
    experiment_tracker=None,
    system_monitor=None,
    session_factory=None,
    audit_runner=None,
) -> FastAPI:
    app = FastAPI(title="AgentBot Platform", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://.*",
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    # Auth middleware — protect all /api/ endpoints except login and bot receiver
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        # Skip auth for: login, bot receiver (internal from localhost), static files
        if path.startswith("/api/") and path != "/api/platform/login" and not path.startswith("/api/bot/"):
            if not _verify_token(request):
                return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        return await call_next(request)

    # Login endpoint
    @app.post("/api/platform/login")
    async def platform_login(body: dict):
        username = body.get("username", "")
        password = body.get("password", "")
        pass_hash = hashlib.sha256(password.encode()).hexdigest()
        if username == _PLATFORM_USER and pass_hash == _PLATFORM_PASS_HASH:
            token = _create_token()
            response = JSONResponse({"token": token, "username": username})
            response.set_cookie("platform_token", token, max_age=_TOKEN_TTL, httponly=False, samesite="lax")
            return response
        raise HTTPException(401, "Invalid credentials")

    @app.get("/api/platform/me")
    async def platform_me(request: Request) -> dict:
        if _verify_token(request):
            return {"username": _PLATFORM_USER}
        raise HTTPException(401, "Unauthorized")

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        result: dict[str, Any] = {"status": "running"}
        if feature_store:
            result["features"] = feature_store.stats
        if position_manager:
            result["positions"] = len(position_manager.open_positions)
            result["exposure_usd"] = position_manager.total_exposure_usd
        if metrics_collector:
            result["metrics_keys"] = len(metrics_collector.snapshot().get("gauges", {}))
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
    def get_all_regimes() -> dict[str, Any]:
        if not regime_detector:
            raise HTTPException(503, "Regime detector not available")
        coins = feature_store.tracked_coins if feature_store else []
        states = regime_detector.detect_all(coins)
        return {coin: state.to_dict() for coin, state in states.items()}

    @app.get("/api/positions")
    def get_positions() -> list[dict]:
        if not position_manager:
            return []
        return [p.to_dict() for p in position_manager.open_positions]

    @app.get("/api/positions/closed")
    def get_closed_positions() -> list[dict]:
        if not position_manager:
            return []
        return [p.to_dict() for p in position_manager.closed_positions[-50:]]

    @app.get("/api/policy")
    def get_policy_state() -> dict[str, Any]:
        if not policy_engine:
            return {}
        return policy_engine.state

    @app.get("/api/risk")
    def get_risk_state() -> dict[str, Any]:
        if not risk_manager:
            return {}
        return risk_manager.state

    @app.get("/api/metrics")
    def get_metrics() -> dict[str, Any]:
        if not metrics_collector:
            return {}
        return metrics_collector.snapshot()

    @app.get("/api/metrics/{name}")
    def get_metric_timeseries(name: str, last_sec: int = 300) -> list[dict]:
        if not metrics_collector:
            return []
        return metrics_collector.get_timeseries(name, last_sec=last_sec)

    @app.get("/api/alerts")
    def get_alerts() -> list[dict]:
        if not alert_manager:
            return []
        return alert_manager.recent_alerts

    @app.get("/api/experiments")
    def list_experiments(status: str | None = None) -> list[dict]:
        if not experiment_tracker:
            return []
        return [e.to_dict() for e in experiment_tracker.list_experiments(status)]

    @app.get("/api/experiments/{name}")
    def get_experiment(name: str) -> dict[str, Any]:
        if not experiment_tracker:
            raise HTTPException(503, "Experiment tracker not available")
        try:
            exps = experiment_tracker.list_experiments()
            for e in exps:
                if e.name == name:
                    return e.to_dict()
            raise HTTPException(404, f"Experiment '{name}' not found")
        except KeyError:
            raise HTTPException(404, f"Experiment '{name}' not found")

    @app.get("/api/experiments/{name}/compare")
    def compare_experiment(name: str) -> dict[str, Any]:
        if not experiment_tracker:
            raise HTTPException(503, "Experiment tracker not available")
        result = experiment_tracker.compare(name)
        if not result:
            raise HTTPException(404, "No results to compare")
        return result

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
    async def get_bot_trades() -> list[dict]:
        if not session_factory:
            return []
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM trade_outcomes ORDER BY exit_time DESC LIMIT 100"))
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    @app.get("/api/bot/trades/stats")
    async def get_bot_trade_stats() -> dict:
        if not session_factory:
            return {}
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
                FROM trade_outcomes
            """))
            row = result.mappings().first()
            if not row or row["total_trades"] == 0:
                return {"total_trades": 0}
            d = dict(row)
            total = d["total_trades"]
            d["win_rate"] = round(float(d["wins"] or 0) / total, 4) if total else 0
            return d

    @app.get("/api/bot/signals")
    async def get_bot_signals() -> list[dict]:
        if not session_factory:
            return []
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM signal_evaluations ORDER BY timestamp DESC LIMIT 200"))
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    @app.get("/api/bot/signals/stats")
    async def get_bot_signal_stats() -> dict:
        if not session_factory:
            return {}
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(text("""
                SELECT action, count(*) as total,
                       round(avg(signal_score)::numeric, 4) as avg_score
                FROM signal_evaluations
                GROUP BY action ORDER BY total DESC
            """))
            rows = result.mappings().all()
            return {"actions": [dict(r) for r in rows]}

    @app.get("/api/bot/regimes")
    async def get_bot_regimes() -> list[dict]:
        if not session_factory:
            return []
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(text("""
                SELECT coin, regime, confidence, trend_strength, volatility_level, timestamp
                FROM regime_labels
                WHERE timestamp > now() - interval '1 hour'
                ORDER BY timestamp DESC
            """))
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

    # ── Research ──

    @app.get("/api/research/queries")
    def list_queries() -> list[str]:
        from ..research.queries import QUERIES
        return list(QUERIES.keys())

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
        async def quant_dataset(date_from: str | None = None) -> dict:
            data = await _enriched_builder.build_with_signals(date_from=date_from)
            return {"trades": len(data["trades"]), "signals": data["signals"]}

        @app.get("/api/quant/metrics")
        async def quant_metrics(date_from: str | None = None) -> dict:
            trades = await _enriched_builder.build(date_from=date_from)
            return _metrics_engine.compute(trades)

        @app.post("/api/quant/experiment")
        async def quant_experiment(config: dict) -> dict:
            trades = await _enriched_builder.build()
            exp_config = ExperimentConfig(**config)
            result = _experiment_engine.run(trades, exp_config)
            return result.to_dict()

        @app.get("/api/quant/analysis")
        async def quant_analysis(date_from: str | None = None) -> dict:
            data = await _enriched_builder.build_with_signals(date_from=date_from)
            return _analysis_engine.analyze(data["trades"], data["signals"])

        @app.get("/api/quant/feature-importance")
        async def quant_feature_importance() -> dict:
            trades = await _enriched_builder.build()
            return _feature_analyzer.analyze(trades)

        @app.get("/api/quant/decisions")
        async def quant_decisions(date_from: str | None = None) -> list[dict]:
            data = await _enriched_builder.build_with_signals(date_from=date_from)
            trades = data["trades"]
            metrics = _metrics_engine.compute(trades)
            analysis = _analysis_engine.analyze(trades, data["signals"])
            decisions = _decision_engine.generate(metrics, analysis, trades)
            return [d.to_dict() for d in decisions]

        @app.get("/api/quant/full")
        async def quant_full(date_from: str | None = None) -> dict:
            """Complete quant report: metrics + analysis + decisions in one call."""
            data = await _enriched_builder.build_with_signals(date_from=date_from)
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
        async def quant_entry_quality(date_from: str | None = None) -> dict:
            from ..quant.analysis.entry_quality import EntryQualityAnalyzer
            trades = await _enriched_builder.build(date_from=date_from)
            analyzer = EntryQualityAnalyzer()
            return analyzer.analyze(trades)

        @app.get("/api/quant/config-analysis")
        async def quant_config_analysis(date_from: str | None = None) -> dict:
            from ..quant.analysis.config_analysis import ConfigAnalyzer
            trades = await _enriched_builder.build(date_from=date_from)
            analyzer = ConfigAnalyzer()
            return analyzer.analyze(trades)

        @app.get("/api/quant/counterfactual")
        async def quant_counterfactual(date_from: str | None = None) -> dict:
            """Counterfactual analysis: what-if at different score thresholds."""
            from sqlalchemy import text as sql_text
            from ..quant.datasets.trades_enriched import _coerce_date
            df = _coerce_date(date_from)
            signals = []
            async with session_factory() as s:
                where = "WHERE timestamp >= :date_from" if df is not None else ""
                params = {"date_from": df} if df is not None else {}
                result = await s.execute(sql_text(f"""
                    SELECT coin, side, signal_score, action, reason, mode,
                           score_min_applied, config_version
                    FROM signal_evaluations {where}
                    ORDER BY timestamp DESC LIMIT 5000
                """), params)
                signals = [dict(r) for r in result.mappings().all()]
            return _counterfactual_analyzer.analyze(signals)

        @app.get("/api/quant/diagnostic")
        async def quant_diagnostic(date_from: str | None = None) -> dict:
            """Diagnostic trace analysis: evaluate ALL filters pass rates."""
            from sqlalchemy import text as sql_text
            from ..quant.datasets.trades_enriched import _coerce_date
            df = _coerce_date(date_from)
            async with session_factory() as s:
                where_parts = ["diagnostic_trace IS NOT NULL"]
                params: dict = {}
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
        async def quant_rejections(date_from: str | None = None) -> dict:
            """Rejection breakdown from signal evaluations with decision_stage."""
            from sqlalchemy import text as sql_text
            params: dict = {}
            where_parts = ["action = 'BLOCKED'"]
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
                result2 = await s.execute(sql_text(f"""
                    SELECT action, count(*) as cnt,
                           round(avg(signal_score)::numeric, 2) as avg_score
                    FROM signal_evaluations
                    {"WHERE timestamp >= :date_from" if date_from else ""}
                    GROUP BY action
                """), params)
                totals = {r["action"]: dict(r) for r in result2.mappings().all()}

                # Stage breakdown
                result3 = await s.execute(sql_text(f"""
                    SELECT decision_stage, count(*) as cnt,
                           round(avg(signal_score)::numeric, 2) as avg_score
                    FROM signal_evaluations
                    {"WHERE timestamp >= :date_from" if date_from else ""}
                    GROUP BY decision_stage
                """), params)
                by_stage = {(r["decision_stage"] or "LEGACY"): dict(r) for r in result3.mappings().all()}

                # Near misses: PRE_CANDIDATE_REJECT where score was within 5 of threshold
                nm_where_parts = ["decision_stage = 'PRE_CANDIDATE_REJECT'"]
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
        async def cleanup_old_snapshots(days: int = 7) -> dict:
            """Delete snapshots older than N days to save disk."""
            from sqlalchemy import text as sql_text
            async with session_factory() as session:
                result = await session.execute(sql_text(
                    "DELETE FROM trade_snapshots WHERE timestamp < NOW() - :days * interval '1 day' RETURNING id"
                ), {"days": days})
                deleted = len(result.fetchall())
                await session.commit()
                return {"deleted": deleted, "days": days}

        @app.get("/api/trades/snapshots/stats")
        async def snapshots_stats() -> dict:
            """How much disk are snapshots using."""
            from sqlalchemy import text as sql_text
            async with session_factory() as session:
                r = await session.execute(sql_text("""
                    SELECT count(*) as total,
                           min(timestamp) as oldest,
                           max(timestamp) as newest,
                           pg_size_pretty(pg_total_relation_size('trade_snapshots')) as disk_size
                    FROM trade_snapshots
                """))
                row = r.mappings().first()
                return dict(row) if row else {}

        @app.get("/api/trades")
        async def list_trades(limit: int = 50, offset: int = 0) -> list[dict]:
            """List all trades with verdict info, paginated."""
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
                    ORDER BY t.entry_time DESC
                    LIMIT :limit OFFSET :offset
                """), {"limit": limit, "offset": offset})
                rows = result.mappings().all()
                return [dict(r) for r in rows]

        @app.get("/api/trades/{trade_id}")
        async def get_trade_detail(trade_id: int):
            """Full trade detail with snapshots, signal, verdict."""
            from sqlalchemy import text as sql_text
            from fastapi.encoders import jsonable_encoder
            async with session_factory() as session:
                # 1. Get trade outcome
                r = await session.execute(
                    sql_text("SELECT * FROM trade_outcomes WHERE id = :id"),
                    {"id": trade_id},
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
                        WHERE coin = :coin AND side = :side
                          AND action = 'ENTER'
                          AND timestamp BETWEEN :t_from AND :t_to
                        ORDER BY ABS(EXTRACT(EPOCH FROM (timestamp - :entry_time)))
                        LIMIT 1
                    """), {
                        "coin": trade["coin"],
                        "side": trade["side"],
                        "entry_time": et,
                        "t_from": et - timedelta(minutes=5),
                        "t_to": et + timedelta(minutes=1),
                    })
                    sig_row = r2.mappings().first()
                    if sig_row:
                        signal = dict(sig_row)

                # 3. Get snapshots
                # trade_snapshots.trade_id is a string key, try matching by coin+side+time range
                snapshots = []
                if trade.get("entry_time") and trade.get("exit_time"):
                    r3 = await session.execute(sql_text("""
                        SELECT * FROM trade_snapshots
                        WHERE coin = :coin AND side = :side
                          AND timestamp BETWEEN :entry_time AND :exit_time
                        ORDER BY timestamp
                    """), {
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
                    cols = [
                        "trade_outcome_id", "coin", "side", "mode",
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
        async def executive_summary() -> dict:
            return await _summary_builder.build()

        @app.get("/api/quant/score-parity")
        async def score_parity() -> dict:
            return await _score_parity_analyzer.analyze()

    # ── Operational Reports ──
    # ── Validation Runner ──
    if session_factory:
        @app.get("/api/quant/validation")
        async def quant_validation(date_from: str | None = None, date_to: str | None = None,
                                   mode: str = "live_trades", coin: str | None = None) -> dict:
            from ..quant.validation.runner import ValidationRunner
            runner = ValidationRunner()
            if mode == "replay_historical":
                coins = [coin] if coin else None
                report = runner.run_full_validation([], mode="replay_historical",
                                                     date_from=date_from, date_to=date_to, coins=coins)
            else:
                trades = await _enriched_builder.build(date_from=date_from)
                report = runner.run_full_validation(trades, mode="live_trades")
            return report.to_dict()

        @app.get("/api/quant/validation/{batch_name}")
        async def quant_validation_batch(batch_name: str, date_from: str | None = None) -> dict:
            from ..quant.validation.runner import ValidationRunner
            trades = await _enriched_builder.build(date_from=date_from)
            runner = ValidationRunner()
            result = runner.run_single_batch(trades, batch_name)
            return result.to_dict()

    # ── Operational Reports ──
    if session_factory:
        from ..research.operational_reports import OperationalReports
        reports = OperationalReports(session_factory)

        @app.get("/api/reports/full")
        async def full_report() -> dict:
            return await reports.full_report()

        @app.get("/api/reports/{name}")
        async def get_report(name: str) -> Any:
            method = getattr(reports, name, None)
            if not method:
                raise HTTPException(404, f"Report '{name}' not found")
            return await method()

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
        async def get_daily_report() -> dict:
            return await daily_gen.generate()

        @app.get("/api/daily-report/{date_str}")
        async def get_daily_report_date(date_str: str) -> dict:
            from datetime import date as d
            report_date = d.fromisoformat(date_str)
            return await daily_gen.generate(report_date)

        @app.get("/api/daily-report/history/list")
        async def daily_report_history() -> list[dict]:
            return await daily_gen.get_history()

    # ── Change Markers ──
    if session_factory:
        from ..quant.markers.marker_service import MarkerService
        _marker_service = MarkerService(session_factory)

        @app.get("/api/markers")
        async def list_markers(limit: int = 50, days: int = 90) -> list[dict]:
            from fastapi.encoders import jsonable_encoder
            return jsonable_encoder(await _marker_service.get_markers(limit=limit, days=days))

        @app.get("/api/markers/recent-impacts")
        async def recent_impacts(limit: int = 10) -> list[dict]:
            from fastapi.encoders import jsonable_encoder
            return jsonable_encoder(await _marker_service.get_recent_with_impact(limit=limit))

        @app.get("/api/markers/{marker_id}")
        async def get_marker_detail(marker_id: int) -> dict:
            from fastapi.encoders import jsonable_encoder
            m = await _marker_service.get_marker(marker_id)
            if not m:
                raise HTTPException(404, "Marker not found")
            return jsonable_encoder(m)

        @app.post("/api/markers/{marker_id}/recalculate")
        async def recalculate_marker(marker_id: int) -> dict:
            return await _marker_service.calculate_impact(marker_id, force=True)

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

    # ── Serve frontend ──
    _static = Path(os.path.dirname(os.path.abspath(__file__))) / "static"
    if _static.exists():
        app.mount("/static", StaticFiles(directory=str(_static)), name="static")

        @app.get("/", response_class=FileResponse)
        async def serve_index():
            return FileResponse(str(_static / "index.html"), media_type="text/html")

    return app
