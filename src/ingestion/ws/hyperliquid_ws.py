"""Hyperliquid WebSocket client — with per-coin health, stale detection,
and dynamic subscribe/unsubscribe without reconnecting.
"""

import asyncio
import json
import time
from collections import defaultdict

import websockets
import structlog

from ..events import TradeEvent, L2BookEvent, EventType, Source
from ..event_bus import EventBus

logger = structlog.get_logger()


class CoinHealth:
    """Per-coin stream health tracking."""
    __slots__ = ("last_trade_ms", "last_book_ms", "trade_count", "book_count")

    def __init__(self) -> None:
        self.last_trade_ms: int = 0
        self.last_book_ms: int = 0
        self.trade_count: int = 0
        self.book_count: int = 0

    @property
    def last_event_ms(self) -> int:
        return max(self.last_trade_ms, self.last_book_ms)

    def is_stale(self, stale_timeout_ms: int) -> bool:
        if self.last_event_ms == 0:
            return False  # never received anything yet
        return (int(time.time() * 1000) - self.last_event_ms) > stale_timeout_ms

    def to_dict(self, stale_timeout_ms: int = 30_000) -> dict:
        now_ms = int(time.time() * 1000)
        return {
            "trades": self.trade_count,
            "books": self.book_count,
            "last_event_ago_ms": now_ms - self.last_event_ms if self.last_event_ms else -1,
            "stale": self.is_stale(stale_timeout_ms),
        }


class HyperliquidWsClient:
    """WebSocket client for Hyperliquid with health tracking and dynamic subs."""

    def __init__(self, event_bus: EventBus, ws_url: str = "wss://api.hyperliquid.xyz/ws",
                 stale_timeout_sec: int = 30) -> None:
        self._bus = event_bus
        self._url = ws_url
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._subscribed_coins: set[str] = set()
        self._desired_coins: set[str] = set()
        self._reconnect_delay = 1.0
        self._stale_timeout_ms = stale_timeout_sec * 1000
        self._health: dict[str, CoinHealth] = defaultdict(CoinHealth)
        self._stats = {"messages": 0, "trades": 0, "books": 0, "errors": 0, "reconnects": 0}

    async def start(self, coins: list[str]) -> None:
        self._running = True
        self._desired_coins = set(coins)

        while self._running:
            try:
                await self._connect_and_listen()
            except Exception:
                self._stats["errors"] += 1
                if not self._running:
                    break
                logger.warning("hl_ws.reconnecting", delay=self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)
                self._stats["reconnects"] += 1

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info("hl_ws.stopped", stats=self._stats)

    async def _connect_and_listen(self) -> None:
        async with websockets.connect(self._url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0
            self._subscribed_coins.clear()

            # Subscribe to all desired coins
            for coin in self._desired_coins:
                await self._subscribe_coin(ws, coin)

            logger.info("hl_ws.connected", coins=len(self._subscribed_coins))

            # Periodic sync task — runs every 5s independently of incoming messages
            sync_task = asyncio.create_task(self._periodic_sync(ws))
            try:
              async for raw_msg in ws:
                if not self._running:
                    break
                self._stats["messages"] += 1

                try:
                    msg = json.loads(raw_msg)
                    channel = msg.get("channel")
                    data = msg.get("data")

                    if channel == "trades" and data:
                        await self._handle_trades(data)
                    elif channel == "l2Book" and data:
                        await self._handle_l2book(data)
                except Exception:
                    logger.exception("hl_ws.parse_error")
                    self._stats["errors"] += 1
            finally:
                sync_task.cancel()

    async def _periodic_sync(self, ws) -> None:
        """Sync subscriptions every 5 seconds — independent of incoming messages."""
        while self._running:
            await asyncio.sleep(5)
            try:
                await self._sync_subscriptions(ws)
            except Exception:
                break  # connection lost, outer loop will reconnect

    async def _subscribe_coin(self, ws, coin: str) -> None:
        await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}))
        await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": coin}}))
        self._subscribed_coins.add(coin)

    async def _unsubscribe_coin(self, ws, coin: str) -> None:
        await ws.send(json.dumps({"method": "unsubscribe", "subscription": {"type": "trades", "coin": coin}}))
        await ws.send(json.dumps({"method": "unsubscribe", "subscription": {"type": "l2Book", "coin": coin}}))
        self._subscribed_coins.discard(coin)

    async def _sync_subscriptions(self, ws) -> None:
        """Add/remove subscriptions without reconnecting."""
        to_add = self._desired_coins - self._subscribed_coins
        to_remove = self._subscribed_coins - self._desired_coins

        for coin in to_add:
            await self._subscribe_coin(ws, coin)
            logger.info("hl_ws.subscribed", coin=coin)

        for coin in to_remove:
            await self._unsubscribe_coin(ws, coin)
            logger.info("hl_ws.unsubscribed", coin=coin)

    async def _handle_trades(self, trades: list[dict]) -> None:
        now_ms = int(time.time() * 1000)
        for t in trades:
            coin = t["coin"]
            self._health[coin].last_trade_ms = now_ms
            self._health[coin].trade_count += 1

            event = TradeEvent(
                source=Source.HYPERLIQUID,
                coin=coin,
                timestamp_ms=t.get("time", now_ms),
                received_ms=now_ms,
                data={
                    "price": float(t["px"]),
                    "size": float(t["sz"]),
                    "side": t.get("side", "").upper(),
                    "hash": t.get("hash", ""),
                },
            )
            await self._bus.publish(event)
            self._stats["trades"] += 1

    async def _handle_l2book(self, book_data: dict) -> None:
        now_ms = int(time.time() * 1000)
        coin = book_data.get("coin", "")
        levels = book_data.get("levels", [[], []])

        self._health[coin].last_book_ms = now_ms
        self._health[coin].book_count += 1

        bids = [[float(l["px"]), float(l["sz"])] for l in levels[0]] if len(levels) > 0 else []
        asks = [[float(l["px"]), float(l["sz"])] for l in levels[1]] if len(levels) > 1 else []

        event = L2BookEvent(
            source=Source.HYPERLIQUID,
            coin=coin,
            timestamp_ms=now_ms,
            received_ms=now_ms,
            data={"bids": bids, "asks": asks},
        )
        await self._bus.publish(event)
        self._stats["books"] += 1

    def update_coins(self, coins: list[str]) -> None:
        """Update desired coins — takes effect on next message (no reconnect)."""
        self._desired_coins = set(coins)

    def get_stale_coins(self) -> list[str]:
        """Return coins with stale data."""
        return [coin for coin, h in self._health.items() if h.is_stale(self._stale_timeout_ms)]

    def get_health(self) -> dict[str, dict]:
        """Per-coin health report."""
        return {coin: h.to_dict(self._stale_timeout_ms) for coin, h in self._health.items()}

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.open

    @property
    def stats(self) -> dict:
        return {**self._stats, "stale_coins": len(self.get_stale_coins())}
