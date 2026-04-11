"""Feature Store: computes and caches feature snapshots per coin.

Subscribes to the EventBus, buffers recent trades/books per coin,
and computes features on demand or periodically. The central interface
for any component needing features (engine, research, ML).
"""

import asyncio
import time
from collections import defaultdict, deque
from typing import Any

import structlog

from ..ingestion.events import Event, EventType
from ..ingestion.event_bus import EventBus
from .base import FeatureComputer, FeatureSnapshot
from .momentum.momentum import MomentumFeatures
from .volatility.volatility import VolatilityFeatures
from .trend.trend import TrendFeatures
from .microstructure.microstructure import MicrostructureFeatures
from .temporal.temporal import TemporalFeatures

logger = structlog.get_logger()


class FeatureStore:
    """Central feature computation and caching service."""

    def __init__(self, event_bus: EventBus, max_trades: int = 5000,
                 max_books: int = 300, cache_ttl_ms: int = 3000) -> None:
        self._bus = event_bus
        self._trades: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=max_trades))
        self._books: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=max_books))
        self._cache: dict[str, FeatureSnapshot] = {}
        self._cache_ttl_ms = cache_ttl_ms
        self._running = False

        # Register all feature computers
        self._computers: list[FeatureComputer] = [
            MomentumFeatures(),
            VolatilityFeatures(),
            TrendFeatures(),
            MicrostructureFeatures(),
            TemporalFeatures(),
        ]

        self._stats = {"computes": 0, "cache_hits": 0, "events_ingested": 0}

    async def start(self) -> None:
        """Subscribe to event bus and start."""
        self._bus.subscribe(self._handle_event, EventType.TRADE)
        self._bus.subscribe(self._handle_event, EventType.L2_BOOK)
        self._running = True
        logger.info("feature_store.started", computers=[c.name for c in self._computers],
                     total_features=sum(len(c.feature_names) for c in self._computers))

    async def stop(self) -> None:
        self._running = False
        logger.info("feature_store.stopped", stats=self._stats)

    async def _handle_event(self, event: Event) -> None:
        """Buffer incoming events by coin."""
        self._stats["events_ingested"] += 1

        if event.type == EventType.TRADE:
            self._trades[event.coin].append(event.to_dict())
        elif event.type == EventType.L2_BOOK:
            self._books[event.coin].append(event.to_dict())

    def get_snapshot(self, coin: str) -> FeatureSnapshot:
        """Get current feature snapshot for a coin. Uses cache if fresh."""
        now_ms = int(time.time() * 1000)

        cached = self._cache.get(coin)
        if cached and (now_ms - cached.timestamp_ms) < self._cache_ttl_ms:
            self._stats["cache_hits"] += 1
            return cached

        snapshot = self._compute(coin, now_ms)
        self._cache[coin] = snapshot
        return snapshot

    def get_all_snapshots(self, coins: list[str] | None = None) -> dict[str, FeatureSnapshot]:
        """Get snapshots for all coins (or specified list)."""
        target_coins = coins or list(self._trades.keys())
        return {coin: self.get_snapshot(coin) for coin in target_coins}

    def _compute(self, coin: str, now_ms: int) -> FeatureSnapshot:
        """Run all feature computers for a coin."""
        trades = list(self._trades.get(coin, []))
        books = list(self._books.get(coin, []))

        all_features: dict[str, float] = {}
        for computer in self._computers:
            try:
                features = computer.compute(coin, trades, books)
                all_features.update(features)
            except Exception:
                logger.exception("feature_store.compute_error", coin=coin, computer=computer.name)

        self._stats["computes"] += 1

        return FeatureSnapshot(
            coin=coin,
            timestamp_ms=now_ms,
            features=all_features,
            version=self._version_string(),
        )

    def _version_string(self) -> str:
        return "+".join(f"{c.name}:{c.version}" for c in self._computers)

    @property
    def feature_names(self) -> list[str]:
        """All feature names across all computers."""
        names = []
        for c in self._computers:
            names.extend(c.feature_names)
        return names

    @property
    def tracked_coins(self) -> list[str]:
        """List of coins currently being tracked."""
        return list(self._trades.keys())

    @property
    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "coins_tracked": len(self._trades),
            "total_features": len(self.feature_names),
        }
