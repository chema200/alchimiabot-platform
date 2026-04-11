"""Database connection management.

Migrations are handled by Alembic — this only manages the connection pool.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import structlog

logger = structlog.get_logger()


class Database:
    """Async database connection pool."""

    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url, pool_size=10, max_overflow=5, echo=False)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init(self) -> None:
        """Check database connectivity. Migrations are handled by Alembic."""
        async with self._engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("database.connected", url=str(self._engine.url).split("@")[-1])

    def session(self) -> AsyncSession:
        return self._session_factory()

    async def close(self) -> None:
        await self._engine.dispose()
