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

from fastapi import APIRouter
from pydantic import BaseModel
import structlog

from ...storage.postgres.models import SignalEvaluation, TradeOutcome, RegimeLabel, TradeSnapshot

logger = structlog.get_logger()

router = APIRouter(prefix="/api/bot", tags=["bot-integration"])


class SignalEvalPayload(BaseModel):
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
    entry_time: str  # ISO format
    exit_time: str   # ISO format


class SnapshotPayload(BaseModel):
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
    status: str | None = None
    mode: str | None = None
    started_at: str | None = None
    uptime_sec: int | None = None
    rejections_total: dict[str, Any] = {}
    sl_viability_top_coins: dict[str, Any] = {}
    sl_viability_config: dict[str, Any] = {}


# Latest gate stats snapshot, in-memory (no DB needed for live telemetry).
# Overwritten on each push from the bot.
_latest_gate_stats: dict[str, Any] = {}
_latest_gate_stats_at: datetime | None = None


def get_latest_gate_stats() -> dict[str, Any]:
    return {
        "received_at": _latest_gate_stats_at.isoformat() if _latest_gate_stats_at else None,
        "data": _latest_gate_stats,
    }


# Session factory — set by the app at startup
_session_factory = None


def set_session_factory(factory):
    global _session_factory
    _session_factory = factory


@router.post("/signal")
async def receive_signal(payload: SignalEvalPayload) -> dict:
    """Receive a signal evaluation from agentbot-live."""
    if not _session_factory:
        return {"ok": False, "error": "DB not ready"}

    # Infer decision_stage from action if not provided (backward compat)
    stage = payload.decision_stage
    if not stage:
        if payload.action == "ENTER":
            stage = "ENTER"
        elif payload.action == "BLOCKED":
            stage = "BLOCKED_POST_CANDIDATE"
        else:
            stage = None

    record = SignalEvaluation(
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
        async with _session_factory() as session:
            session.add(record)
            await session.commit()
        logger.info("signal_ingested", coin=payload.coin, mode=payload.mode,
                    score=payload.signal_score, score_min=payload.score_min_applied,
                    action=payload.action, reason=payload.reason,
                    stage=stage,
                    has_trace=payload.decision_trace is not None,
                    has_diag=payload.diagnostic_trace is not None)
        return {"ok": True}
    except Exception as e:
        logger.warning("bot_receiver.signal_error", error=str(e))
        return {"ok": False, "error": str(e)}


@router.post("/signals")
async def receive_signals(payloads: list[SignalEvalPayload]) -> dict:
    """Receive a batch of signal evaluations."""
    if not _session_factory:
        return {"ok": False, "error": "DB not ready"}

    records = []
    for p in payloads:
        # Infer decision_stage from action if not provided (backward compat)
        stage = p.decision_stage
        if not stage:
            if p.action == "ENTER":
                stage = "ENTER"
            elif p.action == "BLOCKED":
                stage = "BLOCKED_POST_CANDIDATE"
            else:
                stage = None

        records.append(SignalEvaluation(
            user_id=p.user_id,
            coin=p.coin, side=p.side, timestamp=datetime.now(timezone.utc),
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

    try:
        async with _session_factory() as session:
            session.add_all(records)
            await session.commit()
        return {"ok": True, "count": len(records)}
    except Exception as e:
        logger.warning("bot_receiver.signals_error", error=str(e))
        return {"ok": False, "error": str(e)}


@router.post("/trade")
async def receive_trade(payload: TradeOutcomePayload) -> dict:
    """Receive a completed trade from agentbot-live.

    After inserting the trade outcome, attempts to back-link the originating
    ENTER signal_evaluation by matching (coin, side, ~entry_time). This makes
    decision_trace queryable per-trade for ratio/quality analysis.
    """
    if not _session_factory:
        return {"ok": False, "error": "DB not ready"}

    entry_time = datetime.fromisoformat(payload.entry_time)
    exit_time = datetime.fromisoformat(payload.exit_time)

    record = TradeOutcome(
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
        entry_time=entry_time,
        exit_time=exit_time,
    )

    try:
        from sqlalchemy import text as sql_text
        from datetime import timedelta

        async with _session_factory() as session:
            session.add(record)
            await session.flush()  # populate record.id without committing
            trade_id = record.id

            # Back-link most recent matching ENTER signal evaluation.
            # Window: entry_time - 5min .. entry_time + 1min (signal precedes fill).
            # Pick closest in time, only if not already linked (idempotent).
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

        logger.info("bot_receiver.trade", coin=payload.coin, net_pnl=payload.net_pnl,
                    trade_id=trade_id, linked_signal_id=linked_signal_id)
        return {"ok": True, "trade_id": trade_id, "linked_signal_id": linked_signal_id}
    except Exception as e:
        logger.warning("bot_receiver.trade_error", error=str(e))
        return {"ok": False, "error": str(e)}


@router.post("/snapshot")
async def receive_snapshot(payload: SnapshotPayload) -> dict:
    """Receive a position snapshot from agentbot-live (~30s intervals)."""
    if not _session_factory:
        return {"ok": False, "error": "DB not ready"}

    record = TradeSnapshot(
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
        return {"ok": False, "error": str(e)}


@router.post("/gate-stats")
async def receive_gate_stats(payload: GateStatsPayload) -> dict:
    """Receive gate rejection telemetry from agentbot-live.

    Stored in-memory only — this is live telemetry, not historical data.
    The dashboard polls /api/markers/gate-stats to display.
    """
    global _latest_gate_stats, _latest_gate_stats_at
    _latest_gate_stats = payload.model_dump()
    _latest_gate_stats_at = datetime.now(timezone.utc)
    return {"ok": True}


@router.post("/marker")
async def receive_marker(payload: MarkerPayload) -> dict:
    """Receive a change marker from agentbot-live."""
    if not _session_factory:
        return {"ok": False, "error": "DB not ready"}

    try:
        from ...quant.markers.marker_service import MarkerService
        service = MarkerService(_session_factory)
        marker_id = await service.create_marker(**payload.model_dump())
        logger.info("bot_receiver.marker", category=payload.category,
                    label=payload.label, id=marker_id)
        return {"ok": True, "id": marker_id}
    except Exception as e:
        logger.warning("bot_receiver.marker_error", error=str(e))
        return {"ok": False, "error": str(e)}


@router.post("/regime")
async def receive_regime(payload: RegimeLabelPayload) -> dict:
    """Receive regime classification from agentbot-live."""
    if not _session_factory:
        return {"ok": False, "error": "DB not ready"}

    record = RegimeLabel(
        coin=payload.coin, timestamp=datetime.now(timezone.utc),
        regime=payload.regime, confidence=payload.confidence,
        trend_strength=payload.trend_strength,
        volatility_level=payload.volatility_level,
        details=payload.details,
    )

    try:
        async with _session_factory() as session:
            session.add(record)
            await session.commit()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
