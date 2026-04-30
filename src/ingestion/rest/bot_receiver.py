"""Bot Receiver: REST endpoint for agentbot-live to push data.

agentbot-live → POST → platform stores in DB.
If platform is down, bot keeps running — no dependency.

Receives:
  - signal evaluations (every scan cycle)
  - trade outcomes (on close)
  - regime labels (periodic)
"""

from datetime import datetime, timezone
from typing import Any
import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
import structlog

from ...storage.postgres.models import SignalEvaluation, TradeOutcome, RegimeLabel, TradeSnapshot

logger = structlog.get_logger()

router = APIRouter(prefix="/api/bot", tags=["bot-integration"])


class SignalEvalPayload(BaseModel):
    # V83 (2026-04-30) — outbox idempotency key. UUID v4 generado en bot
    # al enqueue. Si está presente, el insert usa ON CONFLICT DO NOTHING.
    # Si None (bot legacy o snapshot directo), comportamiento previo.
    event_id: str | None = None
    user_id: int = 1
    coin: str
    side: str
    signal_score: float = 0
    trend_score: float = 0
    micro_score: float = 0
    momentum_score: float = 0
    regime: str = ""
    mode: str = ""
    price: float = 0
    action: str  # ENTER, SKIP, BLOCKED
    reason: str = ""
    score_min_applied: float | None = None
    config_version: str = ""
    config_snapshot: dict[str, Any] | None = None
    features: dict[str, Any] = {}
    decision_stage: str | None = None
    decision_trace: dict[str, Any] | None = None
    diagnostic_trace: dict[str, Any] | None = None
    entry_diagnostics: dict[str, Any] | None = None
    entry_quality_label: str | None = None
    late_entry_risk: str | None = None


class TradeOutcomePayload(BaseModel):
    event_id: str | None = None  # V83 outbox idempotency
    user_id: int = 1
    coin: str
    side: str
    entry_price: float
    exit_price: float
    size: float
    notional: float = 0
    leverage: int = 3
    gross_pnl: float
    fee: float
    net_pnl: float
    entry_tag: str = ""
    exit_reason: str = ""
    mode: str = ""
    hold_seconds: int = 0
    regime: str = ""
    signal_score: float = 0
    trend_score: float = 0
    micro_score: float = 0
    momentum_score: float = 0
    score_min_applied: float = 0
    config_version: str = ""
    config_snapshot: dict[str, Any] = {}
    mfe_pct: float = 0
    mae_pct: float = 0
    high_water_mark: float = 0
    entry_features: dict[str, Any] = {}
    entry_quality_label: str | None = None
    late_entry_risk: str | None = None
    # Phase A — strategy attribution (2026-04-26).
    strategy_id: int | None = None
    strategy_name: str | None = None
    strategy_template: str | None = None
    entry_time: str  # ISO format
    exit_time: str   # ISO format
    # Fase 2 #6 — provenance del exit_time. Lo aceptamos pero NO lo
    # persistimos aún (ningún column en trade_outcomes). El platform solo
    # lo loggea por ahora; cuando añadamos la columna podremos discriminar
    # trades con timestamps fiables (HL_FILL) de los estimados (FALLBACK_NOW
    # / ENGINE) en análisis de duración / horario / replay.
    # Valores: HL_FILL | ENGINE | MANUAL | RECONCILE | FALLBACK_NOW
    exit_time_source: str | None = None


class SnapshotPayload(BaseModel):
    user_id: int = 1
    trade_id: str
    coin: str
    side: str
    mid_price: float = 0
    sl_price: float = 0
    tp_price: float = 0
    high_water_mark: float = 0
    entry_price: float = 0
    gross_pnl: float = 0
    pnl_pct: float = 0
    hold_seconds: int = 0
    partial_closed: bool = False
    mfe_pct: float = 0
    mae_pct: float = 0


class MarkerPayload(BaseModel):
    event_id: str | None = None  # V83 outbox idempotency
    user_id: int = 1
    category: str
    label: str
    description: str | None = None
    source: str = "USER"
    coin: str | None = None
    side: str | None = None
    mode: str | None = None
    parameter: str | None = None
    old_value: float | None = None
    new_value: float | None = None
    batch_id: str | None = None
    batch_label: str | None = None
    config_snapshot: dict[str, Any] | None = None


class RegimeLabelPayload(BaseModel):
    event_id: str | None = None  # V83 outbox idempotency
    user_id: int = 1
    coin: str
    regime: str
    confidence: float = 0
    trend_strength: float = 0
    volatility_level: float = 0
    details: dict[str, Any] = {}


class GateStatsPayload(BaseModel):
    """Snapshot of gate rejection counters from the bot's entry filter pipeline.

    Pushed periodically by the bot via PlatformBridge.sendGateStats so the
    dashboard can show which filters are bottlenecking entries.
    """
    user_id: int = 1
    status: str | None = None
    mode: str | None = None
    started_at: str | None = None
    uptime_sec: int | None = None
    rejections_total: dict[str, Any] = {}
    sl_viability_top_coins: dict[str, Any] = {}
    sl_viability_config: dict[str, Any] = {}


# Latest gate stats snapshot per user, in-memory (no DB needed for live telemetry).
# Overwritten on each push from the bot.
_latest_gate_stats: dict[int, dict[str, Any]] = {}
_latest_gate_stats_at: dict[int, datetime] = {}


def get_latest_gate_stats(user_id: int = 1) -> dict[str, Any]:
    return {
        "received_at": _latest_gate_stats_at.get(user_id, None),
        "data": _latest_gate_stats.get(user_id, {}),
    }


# Session factory — set by the app at startup
_session_factory = None


def set_session_factory(factory):
    global _session_factory
    _session_factory = factory


# V83 — outbox idempotency helpers.
# Multi-tenant nota: event_id es UUID v4 globalmente único, no hace falta filtrar
# por user_id en la dedupe (un UUID solo puede pertenecer a un user). Pero los
# logs sí incluyen user_id para que la observabilidad multi-usuario sea legible.

def _parse_event_id(value: str | None) -> uuid.UUID | None:
    """Parse event_id string to UUID. Returns None if not provided.

    Raises ValueError if format is invalid (caller should map to HTTP 400).
    """
    if not value:
        return None
    return uuid.UUID(value)


async def _find_by_event_id(session, model, event_id: uuid.UUID | None) -> int | None:
    """Return existing row id if event_id already ingested, else None."""
    if event_id is None:
        return None
    result = await session.execute(
        select(model.id).where(model.event_id == event_id)
    )
    return result.scalar()


def _warn_if_no_event_id(endpoint: str, payload) -> None:
    """V84 (2026-04-30) — WARN when a critical event arrives without
    event_id. Post-V83 the bot ALWAYS generates an event_id at enqueue,
    so missing event_id means: legacy bot version, manual curl, or a
    backfill script that didn't follow the convention. We don't reject
    (idempotency degrades gracefully to "insert and hope") but we log
    visibly so the gap is observable.
    """
    if payload.event_id:
        return
    logger.warning("bot_receiver.no_event_id",
                   endpoint=endpoint,
                   user_id=getattr(payload, "user_id", None),
                   coin=getattr(payload, "coin", None))


@router.post("/signal")
async def receive_signal(payload: SignalEvalPayload) -> dict:
    """Receive a signal evaluation from agentbot-live."""
    if not _session_factory:
        return JSONResponse(status_code=503, content={"ok": False, "error": "DB not ready"})

    # 400 — payload-level validation that pydantic does not cover.
    try:
        eid = _parse_event_id(payload.event_id)
    except (ValueError, TypeError):
        logger.warning("bot_receiver.signal_bad_event_id", user_id=payload.user_id,
                       coin=payload.coin, event_id=payload.event_id)
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid event_id format"})
    _warn_if_no_event_id("/signal", payload)

    # Infer decision_stage from action if not provided (backward compat)
    stage = payload.decision_stage
    if not stage:
        if payload.action == "ENTER":
            stage = "ENTER"
        elif payload.action == "BLOCKED":
            stage = "BLOCKED_POST_CANDIDATE"
        else:
            stage = None

    try:
        async with _session_factory() as session:
            dup_id = await _find_by_event_id(session, SignalEvaluation, eid)
            if dup_id is not None:
                logger.info("bot_receiver.signal_duplicate", user_id=payload.user_id,
                            event_id=str(eid), id=dup_id)
                return {"ok": True, "duplicate": True, "id": dup_id}

            record = SignalEvaluation(
                event_id=eid,
                user_id=payload.user_id,
                coin=payload.coin,
                side=payload.side,
                timestamp=datetime.now(timezone.utc),
                signal_score=payload.signal_score,
                trend_score=payload.trend_score,
                micro_score=payload.micro_score,
                momentum_score=payload.momentum_score,
                regime=payload.regime,
                mode=payload.mode,
                price=payload.price,
                action=payload.action,
                reason=payload.reason,
                score_min_applied=payload.score_min_applied,
                config_version=payload.config_version,
                config_snapshot=payload.config_snapshot or None,
                features=payload.features,
                decision_stage=stage,
                decision_trace=payload.decision_trace or None,
                diagnostic_trace=payload.diagnostic_trace or None,
                entry_diagnostics=payload.entry_diagnostics or None,
                entry_quality_label=payload.entry_quality_label,
                late_entry_risk=payload.late_entry_risk,
            )

            try:
                session.add(record)
                await session.commit()
            except IntegrityError:
                # Race: concurrent insert with same event_id won. Outbox marks ok.
                await session.rollback()
                logger.info("bot_receiver.signal_race_dup", user_id=payload.user_id,
                            event_id=str(eid))
                return {"ok": True, "duplicate": True}

        logger.info("signal_ingested", user_id=payload.user_id, coin=payload.coin,
                    mode=payload.mode, score=payload.signal_score,
                    score_min=payload.score_min_applied, action=payload.action,
                    reason=payload.reason, stage=stage,
                    has_trace=payload.decision_trace is not None,
                    has_diag=payload.diagnostic_trace is not None)
        return {"ok": True}
    except Exception as e:
        logger.warning("bot_receiver.signal_error", user_id=payload.user_id, error=str(e))
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/signals")
async def receive_signals(payloads: list[SignalEvalPayload]) -> dict:
    """Receive a batch of signal evaluations.

    Idempotent: payloads with event_id that already exists are skipped.
    Multi-tenant: mixed user_ids in a single batch are handled correctly
    (event_id is globally unique so user_id filtering is not required for dedupe).
    """
    if not _session_factory:
        return JSONResponse(status_code=503, content={"ok": False, "error": "DB not ready"})

    # 400 — validate all event_ids before touching DB.
    parsed: list[tuple[SignalEvalPayload, uuid.UUID | None]] = []
    missing_count = 0
    for p in payloads:
        try:
            eid = _parse_event_id(p.event_id)
        except (ValueError, TypeError):
            logger.warning("bot_receiver.signals_bad_event_id", user_id=p.user_id,
                           coin=p.coin, event_id=p.event_id)
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": f"invalid event_id format for {p.coin}"})
        if eid is None:
            missing_count += 1
        parsed.append((p, eid))
    # Single aggregated WARN per batch (vs N individual WARNs that would flood the log).
    if missing_count:
        logger.warning("bot_receiver.signals_no_event_id_batch",
                       missing=missing_count, total=len(payloads))

    try:
        async with _session_factory() as session:
            # Bulk dedupe — single IN query.
            existing: set[uuid.UUID] = set()
            event_ids_to_check = [eid for _, eid in parsed if eid is not None]
            if event_ids_to_check:
                res = await session.execute(
                    select(SignalEvaluation.event_id).where(
                        SignalEvaluation.event_id.in_(event_ids_to_check)
                    )
                )
                existing = {row for row in res.scalars()}

            now = datetime.now(timezone.utc)
            records = []
            skipped = 0
            for p, eid in parsed:
                if eid is not None and eid in existing:
                    skipped += 1
                    continue
                stage = p.decision_stage
                if not stage:
                    if p.action == "ENTER":
                        stage = "ENTER"
                    elif p.action == "BLOCKED":
                        stage = "BLOCKED_POST_CANDIDATE"
                    else:
                        stage = None
                records.append(SignalEvaluation(
                    event_id=eid,
                    user_id=p.user_id,
                    coin=p.coin, side=p.side, timestamp=now,
                    signal_score=p.signal_score, trend_score=p.trend_score,
                    micro_score=p.micro_score, momentum_score=p.momentum_score,
                    regime=p.regime, mode=p.mode, price=p.price,
                    action=p.action, reason=p.reason,
                    score_min_applied=p.score_min_applied,
                    config_version=p.config_version,
                    config_snapshot=p.config_snapshot or None,
                    features=p.features,
                    decision_stage=stage,
                    decision_trace=p.decision_trace or None,
                    diagnostic_trace=p.diagnostic_trace or None,
                    entry_diagnostics=p.entry_diagnostics or None,
                    entry_quality_label=p.entry_quality_label,
                    late_entry_risk=p.late_entry_risk,
                ))

            if not records:
                return {"ok": True, "count": 0, "skipped": skipped}

            try:
                session.add_all(records)
                await session.commit()
            except IntegrityError:
                # Race: concurrent inserter beat us on at least one event_id.
                # Fallback to per-record insert so the rest still go through.
                await session.rollback()
                inserted = 0
                race_skipped = 0
                for r in records:
                    try:
                        session.add(r)
                        await session.commit()
                        inserted += 1
                    except IntegrityError:
                        await session.rollback()
                        race_skipped += 1
                logger.info("bot_receiver.signals_partial_race",
                            inserted=inserted, race_skipped=race_skipped,
                            skipped=skipped)
                return {"ok": True, "count": inserted,
                        "skipped": skipped + race_skipped}

        return {"ok": True, "count": len(records), "skipped": skipped}
    except Exception as e:
        logger.warning("bot_receiver.signals_error", error=str(e))
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/trade")
async def receive_trade(payload: TradeOutcomePayload) -> dict:
    """Receive a completed trade from agentbot-live.

    After inserting the trade outcome, attempts to back-link the originating
    ENTER signal_evaluation by matching (coin, side, ~entry_time). This makes
    decision_trace queryable per-trade for ratio/quality analysis.
    """
    if not _session_factory:
        return JSONResponse(status_code=503, content={"ok": False, "error": "DB not ready"})

    # 400 — payload-level validation that pydantic does not cover.
    try:
        eid = _parse_event_id(payload.event_id)
    except (ValueError, TypeError):
        logger.warning("bot_receiver.trade_bad_event_id", user_id=payload.user_id,
                       coin=payload.coin, event_id=payload.event_id)
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid event_id format"})
    _warn_if_no_event_id("/trade", payload)

    try:
        entry_time = datetime.fromisoformat(payload.entry_time)
        exit_time = datetime.fromisoformat(payload.exit_time)
    except (ValueError, TypeError) as e:
        logger.warning("bot_receiver.trade_bad_time", user_id=payload.user_id,
                       coin=payload.coin, entry_time=payload.entry_time,
                       exit_time=payload.exit_time, error=str(e))
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": f"invalid entry_time/exit_time: {e}"})

    try:
        from sqlalchemy import text as sql_text
        from datetime import timedelta

        async with _session_factory() as session:
            # Idempotency — if outbox retried, skip and return existing trade_id.
            dup_id = await _find_by_event_id(session, TradeOutcome, eid)
            if dup_id is not None:
                logger.info("bot_receiver.trade_duplicate", user_id=payload.user_id,
                            coin=payload.coin, event_id=str(eid), trade_id=dup_id)
                return {"ok": True, "duplicate": True, "trade_id": dup_id}

            record = TradeOutcome(
                event_id=eid,
                user_id=payload.user_id,
                coin=payload.coin, side=payload.side,
                entry_price=payload.entry_price, exit_price=payload.exit_price,
                size=payload.size, notional=payload.notional, leverage=payload.leverage,
                gross_pnl=payload.gross_pnl, fee=payload.fee, net_pnl=payload.net_pnl,
                entry_tag=payload.entry_tag, exit_reason=payload.exit_reason,
                mode=payload.mode, hold_seconds=payload.hold_seconds,
                regime=payload.regime, signal_score=payload.signal_score,
                trend_score=payload.trend_score, micro_score=payload.micro_score,
                momentum_score=payload.momentum_score,
                score_min_applied=payload.score_min_applied,
                config_version=payload.config_version,
                mfe_pct=payload.mfe_pct, mae_pct=payload.mae_pct,
                high_water_mark=payload.high_water_mark,
                entry_features=payload.entry_features,
                config_snapshot=payload.config_snapshot or None,
                entry_quality_label=payload.entry_quality_label,
                late_entry_risk=payload.late_entry_risk,
                strategy_id=payload.strategy_id,
                strategy_name=payload.strategy_name,
                strategy_template=payload.strategy_template,
                entry_time=entry_time,
                exit_time=exit_time,
            )

            try:
                session.add(record)
                await session.flush()  # populate record.id without committing
            except IntegrityError:
                # Race: concurrent insert with same event_id won. Fetch its id.
                await session.rollback()
                # New session because rollback may have cleared state.
                async with _session_factory() as session2:
                    dup_id = await _find_by_event_id(session2, TradeOutcome, eid)
                logger.info("bot_receiver.trade_race_dup", user_id=payload.user_id,
                            coin=payload.coin, event_id=str(eid), trade_id=dup_id)
                return {"ok": True, "duplicate": True, "trade_id": dup_id}

            trade_id = record.id

            # Back-link most recent matching ENTER signal evaluation.
            # Window: entry_time - 5min .. entry_time + 1min (signal precedes fill).
            # Pick closest in time, only if not already linked (idempotent).
            # Multi-tenant: filter by user_id so we never link to another user's signal.
            link_result = await session.execute(sql_text("""
                UPDATE signal_evaluations
                SET trade_outcome_id = :trade_id
                WHERE id = (
                    SELECT id FROM signal_evaluations
                    WHERE user_id = :user_id
                      AND coin = :coin
                      AND side = :side
                      AND action = 'ENTER'
                      AND trade_outcome_id IS NULL
                      AND timestamp BETWEEN :t_from AND :t_to
                    ORDER BY ABS(EXTRACT(EPOCH FROM (timestamp - :entry_time)))
                    LIMIT 1
                )
                RETURNING id
            """), {
                "trade_id": trade_id,
                "user_id": payload.user_id,
                "coin": payload.coin,
                "side": payload.side,
                "entry_time": entry_time,
                "t_from": entry_time - timedelta(minutes=5),
                "t_to": entry_time + timedelta(minutes=1),
            })
            linked_signal_id = link_result.scalar()

            await session.commit()

        # exit_time_source visible en log para que la auditoría pueda detectar
        # si llegan FALLBACK_NOW (validator emergency) o si todo el flujo está
        # devolviendo HL_FILL como esperamos (post-fase-2-#6).
        logger.info("bot_receiver.trade", user_id=payload.user_id, coin=payload.coin,
                    net_pnl=payload.net_pnl, trade_id=trade_id,
                    linked_signal_id=linked_signal_id, event_id=str(eid) if eid else None,
                    exit_time_source=payload.exit_time_source)
        # Si llega FALLBACK_NOW, WARN extra — significa que el bot envió un trade
        # cuyo exit_time tuvo que rellenarse en el validador (bug aguas arriba).
        if payload.exit_time_source == "FALLBACK_NOW":
            logger.warning("bot_receiver.trade_fallback_now",
                           user_id=payload.user_id, coin=payload.coin,
                           exit_reason=payload.exit_reason, trade_id=trade_id,
                           note="exit_time was filled by validator (now()) — engine path missed real fill timestamp")
        return {"ok": True, "trade_id": trade_id, "linked_signal_id": linked_signal_id}
    except Exception as e:
        logger.warning("bot_receiver.trade_error", user_id=payload.user_id,
                       coin=payload.coin, error=str(e))
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/snapshot")
async def receive_snapshot(payload: SnapshotPayload) -> dict:
    """Receive a position snapshot from agentbot-live (~30s intervals)."""
    if not _session_factory:
        return JSONResponse(status_code=503, content={"ok": False, "error": "DB not ready"})

    record = TradeSnapshot(
        user_id=payload.user_id,
        trade_id=payload.trade_id,
        coin=payload.coin,
        side=payload.side,
        timestamp=datetime.now(timezone.utc),
        mid_price=payload.mid_price,
        sl_price=payload.sl_price,
        tp_price=payload.tp_price,
        high_water_mark=payload.high_water_mark,
        entry_price=payload.entry_price,
        gross_pnl=payload.gross_pnl,
        pnl_pct=payload.pnl_pct,
        hold_seconds=payload.hold_seconds,
        partial_closed=payload.partial_closed,
        mfe_pct=payload.mfe_pct,
        mae_pct=payload.mae_pct,
    )

    try:
        async with _session_factory() as session:
            session.add(record)
            await session.commit()
        logger.info("bot_receiver.snapshot", trade_id=payload.trade_id,
                    coin=payload.coin, pnl_pct=payload.pnl_pct,
                    hold_s=payload.hold_seconds)
        return {"ok": True}
    except Exception as e:
        logger.warning("bot_receiver.snapshot_error", error=str(e))
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/gate-stats")
async def receive_gate_stats(payload: GateStatsPayload) -> dict:
    """Receive gate rejection telemetry from agentbot-live.

    Stored in-memory only — this is live telemetry, not historical data.
    The dashboard polls /api/markers/gate-stats to display.
    """
    uid = payload.user_id
    _latest_gate_stats[uid] = payload.model_dump()
    _latest_gate_stats_at[uid] = datetime.now(timezone.utc)
    return {"ok": True}


@router.post("/marker")
async def receive_marker(payload: MarkerPayload) -> dict:
    """Receive a change marker from agentbot-live."""
    if not _session_factory:
        return JSONResponse(status_code=503, content={"ok": False, "error": "DB not ready"})

    try:
        eid = _parse_event_id(payload.event_id)
    except (ValueError, TypeError):
        logger.warning("bot_receiver.marker_bad_event_id", user_id=payload.user_id,
                       label=payload.label, event_id=payload.event_id)
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid event_id format"})
    _warn_if_no_event_id("/marker", payload)

    try:
        from ...quant.markers.marker_service import MarkerService
        service = MarkerService(_session_factory)
        # Replace the str event_id from model_dump with the parsed UUID so
        # MarkerService can use it directly with the JSONB cast / ON CONFLICT.
        kwargs = payload.model_dump()
        kwargs["event_id"] = eid
        marker_id = await service.create_marker(**kwargs)
        logger.info("bot_receiver.marker", user_id=payload.user_id,
                    category=payload.category, label=payload.label, id=marker_id)
        return {"ok": True, "id": marker_id}
    except Exception as e:
        logger.warning("bot_receiver.marker_error", user_id=payload.user_id, error=str(e))
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/regime")
async def receive_regime(payload: RegimeLabelPayload) -> dict:
    """Receive regime classification from agentbot-live."""
    if not _session_factory:
        return JSONResponse(status_code=503, content={"ok": False, "error": "DB not ready"})

    try:
        eid = _parse_event_id(payload.event_id)
    except (ValueError, TypeError):
        logger.warning("bot_receiver.regime_bad_event_id", user_id=payload.user_id,
                       coin=payload.coin, event_id=payload.event_id)
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid event_id format"})
    _warn_if_no_event_id("/regime", payload)

    try:
        async with _session_factory() as session:
            dup_id = await _find_by_event_id(session, RegimeLabel, eid)
            if dup_id is not None:
                logger.info("bot_receiver.regime_duplicate", user_id=payload.user_id,
                            event_id=str(eid), id=dup_id)
                return {"ok": True, "duplicate": True, "id": dup_id}

            record = RegimeLabel(
                event_id=eid,
                user_id=payload.user_id,
                coin=payload.coin, timestamp=datetime.now(timezone.utc),
                regime=payload.regime, confidence=payload.confidence,
                trend_strength=payload.trend_strength,
                volatility_level=payload.volatility_level,
                details=payload.details,
            )

            try:
                session.add(record)
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.info("bot_receiver.regime_race_dup", user_id=payload.user_id,
                            event_id=str(eid))
                return {"ok": True, "duplicate": True}

        return {"ok": True}
    except Exception as e:
        logger.warning("bot_receiver.regime_error", user_id=payload.user_id, error=str(e))
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
