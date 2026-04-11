"""Signal Evaluation Service: persists every signal decision.

Records what was seen, what was decided, and why — for every coin scanned.
This is THE critical table for research: it answers "what did we miss?"
and "what should we have skipped?"
"""

from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ...storage.postgres.models import SignalEvaluation
from ..signal.signal_engine import Signal
from ..policy.policy_engine import PolicyDecision

logger = structlog.get_logger()


class EvaluationService:
    """Persists signal evaluations to the database."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._buffer: list[SignalEvaluation] = []
        self._flush_size = 50
        self._stats = {"recorded": 0, "flushed": 0}

    async def record_decision(self, decision: PolicyDecision, trade_outcome_id: int | None = None) -> None:
        """Record a policy decision (ENTER, SKIP, or BLOCKED)."""
        signal = decision.signal

        record = SignalEvaluation(
            coin=signal.coin,
            side=signal.side,
            timestamp=datetime.now(timezone.utc),
            signal_score=signal.score,
            trend_score=signal.trend_score,
            micro_score=signal.micro_score,
            momentum_score=signal.momentum_score,
            regime=signal.regime.value,
            price=signal.features.get("mom_ret_1m", 0),  # last price proxy
            action=decision.action,
            reason=decision.reason,
            features=signal.features,
            trade_outcome_id=trade_outcome_id,
        )

        self._buffer.append(record)
        self._stats["recorded"] += 1

        if len(self._buffer) >= self._flush_size:
            await self.flush()

    async def record_signal_scan(self, signals: list[Signal], decisions: list[PolicyDecision]) -> None:
        """Record a batch of signals from a scan cycle."""
        for decision in decisions:
            await self.record_decision(decision)

    async def flush(self) -> None:
        """Write buffered evaluations to DB."""
        if not self._buffer:
            return

        batch = self._buffer[:]
        self._buffer.clear()

        try:
            async with self._session_factory() as session:
                session.add_all(batch)
                await session.commit()
            self._stats["flushed"] += len(batch)
        except Exception:
            logger.exception("evaluation_service.flush_error", count=len(batch))
            # Put back for retry
            self._buffer.extend(batch)

    @property
    def stats(self) -> dict[str, Any]:
        return {**self._stats, "buffered": len(self._buffer)}
