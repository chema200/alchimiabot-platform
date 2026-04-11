"""Standard event schema for all ingested data.

Every piece of market data flows through the system as an Event.
This ensures consistency between live, replay, and backtest modes.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import time


class EventType(str, Enum):
    TRADE = "trade"
    L2_BOOK = "l2_book"
    BBO = "bbo"
    CANDLE = "candle"
    FUNDING = "funding"
    LIQUIDATION = "liquidation"
    OPEN_INTEREST = "open_interest"
    ACCOUNT_UPDATE = "account_update"
    FILL = "fill"
    ORDER_UPDATE = "order_update"


class Source(str, Enum):
    HYPERLIQUID = "hyperliquid"
    BINANCE = "binance"


@dataclass(slots=True)
class Event:
    """Normalized market data event — the universal unit of data in the platform."""

    type: EventType
    source: Source
    coin: str
    timestamp_ms: int  # exchange timestamp in milliseconds
    received_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def latency_ms(self) -> int:
        return self.received_ms - self.timestamp_ms

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "source": self.source.value,
            "coin": self.coin,
            "timestamp_ms": self.timestamp_ms,
            "received_ms": self.received_ms,
            "latency_ms": self.latency_ms,
            **self.data,
        }


@dataclass(slots=True)
class TradeEvent(Event):
    """Individual trade execution."""

    type: EventType = field(default=EventType.TRADE, init=False)

    @property
    def price(self) -> float:
        return self.data.get("price", 0.0)

    @property
    def size(self) -> float:
        return self.data.get("size", 0.0)

    @property
    def side(self) -> str:
        return self.data.get("side", "")


@dataclass(slots=True)
class L2BookEvent(Event):
    """Level 2 orderbook snapshot."""

    type: EventType = field(default=EventType.L2_BOOK, init=False)

    @property
    def bids(self) -> list[list[float]]:
        return self.data.get("bids", [])

    @property
    def asks(self) -> list[list[float]]:
        return self.data.get("asks", [])

    @property
    def mid(self) -> float:
        if self.bids and self.asks:
            return (self.bids[0][0] + self.asks[0][0]) / 2
        return 0.0

    @property
    def spread_bps(self) -> float:
        if self.bids and self.asks and self.mid > 0:
            return (self.asks[0][0] - self.bids[0][0]) / self.mid * 10000
        return 0.0
