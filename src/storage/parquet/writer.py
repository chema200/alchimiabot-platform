"""Parquet writer — append-only, partitioned by event_type/coin/date/hour.

Never rewrites existing files. Each flush creates a new chunk file.
Compaction (merging small chunks) is a separate job.

Partition scheme:
    data/raw/{event_type}/{coin}/{YYYY-MM-DD}/{HH}_{chunk_id}.parquet
"""

import asyncio
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from ...ingestion.events import Event

logger = structlog.get_logger()


class ParquetWriter:
    """Async-compatible append-only Parquet writer."""

    def __init__(self, base_dir: str = "data/raw", flush_interval_sec: int = 60,
                 flush_size: int = 5000, partition_by_hour: bool = True,
                 compression: str = "snappy") -> None:
        self._base_dir = base_dir
        self._flush_interval = flush_interval_sec
        self._flush_size = flush_size
        self._partition_by_hour = partition_by_hour
        self._compression = compression
        self._buffers: dict[str, list[dict]] = defaultdict(list)
        self._running = False
        self._stats = {"events": 0, "flushes": 0, "files_written": 0, "rows_written": 0}

    async def handle_event(self, event: Event) -> None:
        """EventBus handler — buffer incoming events."""
        key = f"{event.type.value}/{event.coin}"
        self._buffers[key].append(event.to_dict())
        self._stats["events"] += 1

        if len(self._buffers[key]) >= self._flush_size:
            await self._flush_buffer(key)

    async def start(self) -> None:
        """Start periodic flush loop."""
        self._running = True
        logger.info("parquet_writer.started", base_dir=self._base_dir)
        while self._running:
            await asyncio.sleep(self._flush_interval)
            await self.flush_all()

    async def stop(self) -> None:
        self._running = False
        await self.flush_all()
        logger.info("parquet_writer.stopped", stats=self._stats)

    async def flush_all(self) -> None:
        keys = list(self._buffers.keys())
        for key in keys:
            if self._buffers[key]:
                await self._flush_buffer(key)

    async def _flush_buffer(self, key: str) -> None:
        rows = self._buffers.pop(key, [])
        if not rows:
            return
        await asyncio.get_event_loop().run_in_executor(None, self._write_parquet, key, rows)
        self._stats["flushes"] += 1

    def _write_parquet(self, key: str, rows: list[dict]) -> None:
        """Write rows as a new chunk file — never overwrites.

        Partitions by the event's timestamp_ms, not by flush time.
        This ensures replay/backfill data lands in the correct partition.
        """
        # Group rows by their actual event hour
        from collections import defaultdict as _dd
        by_partition: dict[tuple[str, str], list[dict]] = _dd(list)

        for row in rows:
            ts_ms = row.get("timestamp_ms", 0)
            if ts_ms > 0:
                event_time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            else:
                event_time = datetime.now(timezone.utc)
            date_str = event_time.strftime("%Y-%m-%d")
            hour_str = event_time.strftime("%H")
            by_partition[(date_str, hour_str)].append(row)

        for (date_str, hour_str), partition_rows in by_partition.items():
            chunk_id = uuid.uuid4().hex[:8]

            if self._partition_by_hour:
                dir_path = os.path.join(self._base_dir, key, date_str)
                file_name = f"{hour_str}_{chunk_id}.parquet"
            else:
                dir_path = os.path.join(self._base_dir, key)
                file_name = f"{date_str}_{chunk_id}.parquet"

            os.makedirs(dir_path, exist_ok=True)
            file_path = os.path.join(dir_path, file_name)

            table = pa.Table.from_pylist(partition_rows)
            pq.write_table(table, file_path, compression=self._compression)

            self._stats["files_written"] += 1
            self._stats["rows_written"] += len(partition_rows)
            logger.debug("parquet_writer.flushed", key=key, rows=len(partition_rows), file=file_path)

    @property
    def stats(self) -> dict:
        return dict(self._stats)
