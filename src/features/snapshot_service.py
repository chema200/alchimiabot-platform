"""Feature Snapshot Service: periodically persists feature snapshots to DB.

Enables offline research queries like:
  "What did BTC features look like at 14:00 on April 3rd?"
  "Show me all coins where trend_strength > 0.5 in the last week"
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from .store import FeatureStore
from .contract import FEATURE_VERSION, validate_snapshot
from ..storage.postgres.models import FeatureSnapshotRecord

logger = structlog.get_logger()


class SnapshotService:
    """Persists feature snapshots at regular intervals."""

    def __init__(self, feature_store: FeatureStore, session_factory,
                 interval_sec: int = 60, coins: list[str] | None = None) -> None:
        self._fs = feature_store
        self._session_factory = session_factory
        self._interval = interval_sec
        self._coins = coins
        self._running = False
        self._stats = {"snapshots": 0, "errors": 0}

    async def start(self) -> None:
        """Start periodic snapshot loop."""
        self._running = True
        logger.info("snapshot_service.started", interval=self._interval)

        while self._running:
            await asyncio.sleep(self._interval)
            await self._take_snapshots()

    async def stop(self) -> None:
        self._running = False
        logger.info("snapshot_service.stopped", stats=self._stats)

    async def _take_snapshots(self) -> None:
        """Snapshot all tracked coins."""
        coins = self._coins or self._fs.tracked_coins
        if not coins:
            return

        now = datetime.now(timezone.utc)
        records = []

        for coin in coins:
            try:
                snap = self._fs.get_snapshot(coin)
                errors = validate_snapshot(snap.features)
                if errors:
                    logger.warning("snapshot_service.validation_errors", coin=coin, errors=errors[:3])

                records.append(FeatureSnapshotRecord(
                    coin=coin,
                    timestamp=now,
                    version=FEATURE_VERSION,
                    features=snap.features,
                ))
            except Exception:
                self._stats["errors"] += 1
                logger.exception("snapshot_service.error", coin=coin)

        if records:
            try:
                async with self._session_factory() as session:
                    session.add_all(records)
                    await session.commit()
                self._stats["snapshots"] += len(records)
            except Exception:
                self._stats["errors"] += 1
                logger.exception("snapshot_service.flush_error")

    @property
    def stats(self) -> dict[str, Any]:
        return dict(self._stats)
