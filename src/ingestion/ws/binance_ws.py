"""Binance Futures WebSocket client for trend context data.

Provides supplementary market data (klines, trades) from Binance Futures
for cross-exchange context. Normalizes into the same Event schema.
"""

import asyncio
import json
import time
import websockets
import structlog

from ..events import TradeEvent, Event, EventType, Source
from ..event_bus import EventBus

logger = structlog.get_logger()

# Binance symbol mapping: BTCUSDT -> BTC
SYMBOL_TO_COIN = {}


def _coin_from_symbol(symbol: str) -> str:
    if symbol in SYMBOL_TO_COIN:
        return SYMBOL_TO_COIN[symbol]
    coin = symbol.replace("USDT", "").replace("BUSD", "")
    SYMBOL_TO_COIN[symbol] = coin
    return coin


class BinanceWsClient:
    """WebSocket client for Binance Futures market data (trend context)."""

    def __init__(self, event_bus: EventBus, ws_url: str = "wss://fstream.binance.com/ws") -> None:
        self._bus = event_bus
        self._base_url = ws_url
        self._running = False
        self._ws = None
        self._coins: list[str] = []
        self._reconnect_delay = 1.0
        self._stats = {"messages": 0, "trades": 0, "errors": 0, "reconnects": 0}

    async def start(self, coins: list[str]) -> None:
        """Start listening to Binance streams for the given coins."""
        self._running = True
        self._coins = coins

        while self._running:
            try:
                await self._connect_and_listen()
            except Exception:
                self._stats["errors"] += 1
                if not self._running:
                    break
                logger.warning("binance_ws.reconnecting", delay=self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)
                self._stats["reconnects"] += 1

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info("binance_ws.stopped", stats=self._stats)

    async def _connect_and_listen(self) -> None:
        # Combined stream: btcusdt@aggTrade/ethusdt@aggTrade/...
        streams = "/".join(f"{c.lower()}usdt@aggTrade" for c in self._coins)
        url = f"{self._base_url}/{streams}"

        async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0
            logger.info("binance_ws.connected", coins=len(self._coins))

            async for raw_msg in ws:
                if not self._running:
                    break
                self._stats["messages"] += 1

                try:
                    msg = json.loads(raw_msg)
                    if msg.get("e") == "aggTrade":
                        await self._handle_agg_trade(msg)
                except Exception:
                    logger.exception("binance_ws.parse_error")
                    self._stats["errors"] += 1

    async def _handle_agg_trade(self, t: dict) -> None:
        coin = _coin_from_symbol(t.get("s", ""))
        event = TradeEvent(
            source=Source.BINANCE,
            coin=coin,
            timestamp_ms=t.get("T", int(time.time() * 1000)),
            data={
                "price": float(t["p"]),
                "size": float(t["q"]),
                "side": "SELL" if t.get("m", False) else "BUY",
                "agg_id": t.get("a"),
            },
        )
        await self._bus.publish(event)
        self._stats["trades"] += 1

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.open

    @property
    def stats(self) -> dict:
        return dict(self._stats)
