"""Replay Engine: event-driven historical simulation.

Replays raw events from Parquet files through the same pipeline
as live trading. Deterministic — same events, same features, same decisions.
Used for backtesting and auditing live vs simulated performance.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import pyarrow.parquet as pq
import structlog

from ..ingestion.events import Event, EventType, Source

logger = structlog.get_logger()


@dataclass
class ReplayConfig:
    data_dir: str = "data/raw"
    speed: float = 0.0          # 0 = max speed, 1.0 = real-time, 2.0 = 2x
    start_date: str = ""        # YYYY-MM-DD, empty = all
    end_date: str = ""          # YYYY-MM-DD, empty = all
    coins: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=lambda: ["trade", "l2_book"])


@dataclass
class ReplayStats:
    events_replayed: int = 0
    events_skipped: int = 0
    coins_seen: set[str] = field(default_factory=set)
    start_ts: int = 0
    end_ts: int = 0
    elapsed_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "events_replayed": self.events_replayed,
            "events_skipped": self.events_skipped,
            "coins": len(self.coins_seen),
            "time_range_sec": (self.end_ts - self.start_ts) / 1000 if self.start_ts else 0,
            "elapsed_sec": round(self.elapsed_sec, 2),
        }


class ReplayEngine:
    """Replays historical events from Parquet storage."""

    def __init__(self, config: ReplayConfig | None = None) -> None:
        self._config = config or ReplayConfig()
        self._handlers: list[Callable[[Event], Awaitable[None]]] = []
        self._running = False
        self._stats = ReplayStats()

    def on_event(self, handler: Callable[[Event], Awaitable[None]]) -> None:
        """Register an event handler (feature store, engine, etc.)."""
        self._handlers.append(handler)

    async def run(self) -> ReplayStats:
        """Replay all matching events in chronological order."""
        import time as _time

        self._running = True
        events = self._load_events()
        events.sort(key=lambda e: e.get("timestamp_ms", 0))

        logger.info("replay.starting", events=len(events))
        start = _time.time()

        last_ts = 0
        for row in events:
            if not self._running:
                break

            event = self._row_to_event(row)
            if event is None:
                self._stats.events_skipped += 1
                continue

            # Speed control
            if self._config.speed > 0 and last_ts > 0:
                delta_ms = event.timestamp_ms - last_ts
                if delta_ms > 0:
                    import asyncio
                    await asyncio.sleep(delta_ms / 1000 / self._config.speed)

            # Dispatch to all handlers concurrently
            if len(self._handlers) == 1:
                await self._handlers[0](event)
            else:
                await asyncio.gather(*(h(event) for h in self._handlers), return_exceptions=True)

            self._stats.events_replayed += 1
            self._stats.coins_seen.add(event.coin)

            if self._stats.start_ts == 0:
                self._stats.start_ts = event.timestamp_ms
            self._stats.end_ts = event.timestamp_ms
            last_ts = event.timestamp_ms

        self._stats.elapsed_sec = _time.time() - start
        logger.info("replay.completed", **self._stats.to_dict())
        return self._stats

    def stop(self) -> None:
        self._running = False

    def _load_events(self) -> list[dict]:
        """Load events from Parquet files matching config filters.

        Supports partitioned layout: {data_dir}/{event_type}/{coin}/{date}/{hour_chunk}.parquet
        Also supports flat layout: {data_dir}/{event_type}/{coin}/{date}.parquet
        """
        import glob as globmod

        all_rows = []
        cfg = self._config

        for event_type in cfg.event_types:
            type_dir = os.path.join(cfg.data_dir, event_type)
            if not os.path.exists(type_dir):
                continue

            for coin_dir in sorted(os.listdir(type_dir)):
                if cfg.coins and coin_dir not in cfg.coins:
                    continue

                coin_path = os.path.join(type_dir, coin_dir)
                if not os.path.isdir(coin_path):
                    continue

                # Find all .parquet files recursively (supports both flat and partitioned)
                pattern = os.path.join(coin_path, "**", "*.parquet")
                for file_path in sorted(globmod.glob(pattern, recursive=True)):
                    # Extract date from path for filtering
                    # Path could be: .../coin/2026-04-05/14_abc123.parquet
                    # Or: .../coin/2026-04-05.parquet
                    parts = file_path.replace(coin_path, "").strip(os.sep).split(os.sep)
                    date_str = parts[0] if parts else ""
                    # Remove .parquet extension if flat layout
                    if date_str.endswith(".parquet"):
                        date_str = date_str.replace(".parquet", "")

                    if cfg.start_date and date_str < cfg.start_date:
                        continue
                    if cfg.end_date and date_str > cfg.end_date:
                        continue

                    try:
                        table = pq.read_table(file_path)
                        rows = table.to_pylist()
                        for row in rows:
                            row["_event_type"] = event_type
                        all_rows.extend(rows)
                    except Exception:
                        logger.warning("replay.read_error", file=file_path)

        logger.info("replay.loaded", rows=len(all_rows), types=cfg.event_types)
        return all_rows

    @staticmethod
    def _row_to_event(row: dict) -> Event | None:
        """Convert a Parquet row back to an Event."""
        try:
            event_type = EventType(row.get("_event_type", row.get("type", "")))
            source = Source(row.get("source", "hyperliquid"))

            return Event(
                type=event_type,
                source=source,
                coin=row.get("coin", ""),
                timestamp_ms=row.get("timestamp_ms", 0),
                received_ms=row.get("received_ms", 0),
                data={k: v for k, v in row.items()
                      if k not in ("type", "source", "coin", "timestamp_ms", "received_ms", "_event_type", "latency_ms")},
            )
        except (ValueError, KeyError):
            return None
