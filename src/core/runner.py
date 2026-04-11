"""Platform Runner: builds, starts, and stops all services.

Separated from main.py so different modes (live, replay, ingestion-only,
feature-backfill, training) can reuse the same wiring.
"""

import asyncio
import signal
from enum import Enum

import structlog

from config import settings
from ..ingestion.event_bus import EventBus
from ..ingestion.ws.hyperliquid_ws import HyperliquidWsClient
from ..ingestion.ws.binance_ws import BinanceWsClient
from ..storage.parquet.writer import ParquetWriter
from ..storage.postgres.database import Database
from ..features.store import FeatureStore
from ..observability.alerts.telegram_summary import TelegramSummaryService
from ..features.snapshot_service import SnapshotService
from ..observability.health.system_monitor import SystemMonitor
from ..dashboard.server import DashboardServer
from ..audit.runner import AuditRunner
from ..audit.checks.integration_check import IntegrationCheck
from ..audit.checks.data_quality_check import DataQualityCheck
from ..audit.checks.storage_check import StorageCheck
from ..audit.checks.consistency_check import ConsistencyCheck

logger = structlog.get_logger()


class RunMode(str, Enum):
    LIVE = "live"               # full platform: ingestion + features + storage + snapshots
    INGEST_ONLY = "ingest_only" # only capture data, no features/engine
    REPLAY = "replay"           # replay historical data
    BACKFILL = "backfill"       # recompute features from raw data


class PlatformRunner:
    """Orchestrates all platform services."""

    def __init__(self, mode: RunMode = RunMode.LIVE, coins: list[str] | None = None) -> None:
        self._mode = mode
        self._coins = coins or settings.coins.default_coins
        self._shutdown = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        # Components — initialized in build()
        self.event_bus: EventBus | None = None
        self.db: Database | None = None
        self.parquet: ParquetWriter | None = None
        self.hl_ws: HyperliquidWsClient | None = None
        self.binance_ws: BinanceWsClient | None = None
        self.feature_store: FeatureStore | None = None
        self.snapshot_service: SnapshotService | None = None
        self.system_monitor: SystemMonitor | None = None
        self.dashboard: DashboardServer | None = None
        self.audit_runner: AuditRunner | None = None
        self.telegram_summary: TelegramSummaryService | None = None

    async def build(self) -> None:
        """Create and wire all components."""
        logger.info("platform.building", mode=self._mode.value, coins=len(self._coins))

        self.event_bus = EventBus()
        self.db = Database(settings.db.url)
        self.parquet = ParquetWriter(
            base_dir=settings.parquet.base_dir,
            flush_interval_sec=settings.parquet.flush_interval_sec,
            flush_size=settings.parquet.flush_size,
            partition_by_hour=settings.parquet.partition_by_hour,
        )

        # Subscribe storage to all events (critical — data must not be lost)
        self.event_bus.subscribe(self.parquet.handle_event, name="parquet_writer", critical=True)

        if self._mode in (RunMode.LIVE, RunMode.INGEST_ONLY):
            self.hl_ws = HyperliquidWsClient(
                self.event_bus, ws_url=settings.hl.ws_url,
                stale_timeout_sec=settings.hl.stale_timeout_sec,
            )
            if settings.binance.enabled:
                self.binance_ws = BinanceWsClient(self.event_bus, ws_url=settings.binance.ws_url)

        if self._mode in (RunMode.LIVE, RunMode.BACKFILL):
            self.feature_store = FeatureStore(
                self.event_bus,
                max_trades=settings.features.max_trades_per_coin,
                max_books=settings.features.max_books_per_coin,
                cache_ttl_ms=settings.features.cache_ttl_ms,
            )
            # Persist feature snapshots every 60s for research
            self.snapshot_service = SnapshotService(
                self.feature_store, self.db.session, interval_sec=60, coins=self._coins,
            )

        # System monitor
        self.system_monitor = SystemMonitor(data_dirs={
            "raw": settings.parquet.base_dir,
            "processed": settings.storage.processed_dir,
            "datasets": settings.storage.datasets_dir,
            "logs": "logs",
        })

        # Audit system
        self.audit_runner = AuditRunner(self.db.session)
        self.audit_runner.register(IntegrationCheck(self.db.session), interval_sec=300)   # 5 min
        self.audit_runner.register(DataQualityCheck(self.db.session), interval_sec=900)   # 15 min
        self.audit_runner.register(StorageCheck(), interval_sec=3600)                     # 1 hour
        self.audit_runner.register(ConsistencyCheck(self.db.session), interval_sec=21600) # 6 hours

        # Telegram summary every 4 hours
        self.telegram_summary = TelegramSummaryService(self.db.session, interval_hours=4)

        # Dashboard API server (port 8090)
        self.dashboard = DashboardServer(
            port=8090,
            feature_store=self.feature_store,
            system_monitor=self.system_monitor,
            session_factory=self.db.session if self.db else None,
            audit_runner=self.audit_runner,
        )

        await self.db.init()
        if self.feature_store:
            await self.feature_store.start()

    async def run(self) -> None:
        """Start all tasks and wait for shutdown signal."""
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_signal)

        # Start core tasks
        self._tasks.append(asyncio.create_task(self.event_bus.start()))
        self._tasks.append(asyncio.create_task(self.parquet.start()))

        # Start ingestion
        if self.hl_ws:
            self._tasks.append(asyncio.create_task(self.hl_ws.start(self._coins)))
        if self.binance_ws:
            binance_coins = self._coins[:settings.binance.max_coins]
            self._tasks.append(asyncio.create_task(self.binance_ws.start(binance_coins)))

        # Start snapshot persistence
        if self.snapshot_service:
            self._tasks.append(asyncio.create_task(self.snapshot_service.start()))

        # Start audit system
        if self.audit_runner:
            self._tasks.append(asyncio.create_task(self.audit_runner.start()))

        # Start Telegram summary
        if self.telegram_summary:
            self._tasks.append(asyncio.create_task(self.telegram_summary.start()))

        # Start dashboard API
        if self.dashboard:
            self._tasks.append(asyncio.create_task(self.dashboard.start()))

        logger.info("platform.started", mode=self._mode.value, tasks=len(self._tasks))
        await self._shutdown.wait()

    async def stop(self) -> None:
        """Graceful shutdown of all services."""
        logger.info("platform.stopping")

        if self.hl_ws:
            await self.hl_ws.stop()
        if self.binance_ws:
            await self.binance_ws.stop()
        if self.telegram_summary:
            await self.telegram_summary.stop()
        if self.audit_runner:
            await self.audit_runner.stop()
        if self.dashboard:
            await self.dashboard.stop()
        if self.snapshot_service:
            await self.snapshot_service.stop()
        if self.feature_store:
            await self.feature_store.stop()
        await self.event_bus.stop()
        await self.parquet.stop()
        await self.db.close()

        for task in self._tasks:
            task.cancel()

        logger.info("platform.stopped")

    def _handle_signal(self) -> None:
        logger.info("platform.shutdown_requested")
        self._shutdown.set()
