"""Dashboard Server: runs FastAPI with uvicorn as part of the platform."""

import asyncio

import uvicorn
import structlog

from .api import create_app

logger = structlog.get_logger()


class DashboardServer:
    """Runs the dashboard API server."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8190, **api_kwargs) -> None:
        self._host = host
        self._port = port
        self._app = create_app(**api_kwargs)
        self._server = None

    async def start(self) -> None:
        """Start the uvicorn server as an async task."""
        config = uvicorn.Config(self._app, host=self._host, port=self._port, log_level="warning")
        self._server = uvicorn.Server(config)
        logger.info("dashboard.started", host=self._host, port=self._port)
        await self._server.serve()

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        logger.info("dashboard.stopped")
