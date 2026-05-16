"""Database connection management.

Migrations are handled by Alembic — this only manages the connection pool.
"""

import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import structlog

logger = structlog.get_logger()


class Database:
    """Async database connection pool."""

    def __init__(self, url: str) -> None:
        # P2 audit-2026-05-15 — pool sizing para 15+ users concurrentes.
        # Pre-fix: pool_size=10 + max_overflow=5 = 15 conexiones tope.
        # Cuando 15 users abren simultaneamente /api/quant/full (que hace
        # 4-5 queries serial), el pool se satura y empiezan timeouts.
        # Default subido a 25 + 15. Override via env DB_POOL_SIZE /
        # DB_POOL_OVERFLOW para deployments grandes/pequenos.
        pool_size = int(os.getenv("DB_POOL_SIZE", "25"))
        max_overflow = int(os.getenv("DB_POOL_OVERFLOW", "15"))
        self._engine = create_async_engine(
            url, pool_size=pool_size, max_overflow=max_overflow,
            pool_pre_ping=True,  # detect connections muertas (TCP timeout, DB restart) sin tirar la request
            echo=False,
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        logger.info("database.pool_init", pool_size=pool_size, max_overflow=max_overflow)

    async def init(self) -> None:
        """Check database connectivity. Migrations are handled by Alembic."""
        async with self._engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("database.connected", url=str(self._engine.url).split("@")[-1])

    def session(self) -> AsyncSession:
        return self._session_factory()

    async def close(self) -> None:
        await self._engine.dispose()
