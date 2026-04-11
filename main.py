"""AgentBot Platform — entry point.

Usage:
    python main.py                          # live mode, default coins
    python main.py --coins BTC,ETH,SOL      # live mode, specific coins
    python main.py --mode ingest_only       # capture data only
"""

import asyncio
import click
import structlog

from config import settings
from src.core.runner import PlatformRunner, RunMode

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()


@click.command()
@click.option("--coins", default=None, help="Comma-separated list of coins (default: from config)")
@click.option("--mode", default="live", type=click.Choice(["live", "ingest_only", "replay", "backfill"]))
def main(coins: str | None, mode: str) -> None:
    coin_list = [c.strip().upper() for c in coins.split(",")] if coins else None
    run_mode = RunMode(mode)
    asyncio.run(run(run_mode, coin_list))


async def run(mode: RunMode, coins: list[str] | None) -> None:
    runner = PlatformRunner(mode=mode, coins=coins)
    await runner.build()

    try:
        await runner.run()
    finally:
        await runner.stop()


if __name__ == "__main__":
    main()
