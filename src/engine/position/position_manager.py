"""Position Manager: tracks open positions, updates SL/TP, triggers exits.

Maintains the lifecycle of each position from entry to exit,
including trailing stops, partial closes, and timeout management.
"""

from dataclasses import dataclass, field
from typing import Any
import time

import structlog

from ..risk.risk_manager import RiskLevels

logger = structlog.get_logger()


@dataclass
class Position:
    """A live trading position."""
    coin: str
    side: str
    entry_price: float
    size_coins: float
    size_usd: float
    leverage: int
    sl_price: float
    tp_price: float
    trailing_activation_pct: float
    trailing_distance_pct: float

    entry_time: float = field(default_factory=time.time)
    current_price: float = 0.0
    high_water_mark: float = 0.0
    low_water_mark: float = float("inf")
    partial_closed: bool = False
    partial_pnl: float = 0.0
    exit_price: float = 0.0
    exit_time: float = 0.0
    exit_reason: str = ""
    entry_tag: str = ""
    regime: str = ""
    signal_score: float = 0.0
    trend_score: float = 0.0
    micro_score: float = 0.0

    @property
    def hold_sec(self) -> float:
        end = self.exit_time if self.exit_time > 0 else time.time()
        return end - self.entry_time

    @property
    def unrealized_pnl(self) -> float:
        if self.current_price <= 0:
            return 0.0
        if self.side == "LONG":
            return (self.current_price - self.entry_price) * self.size_coins
        return (self.entry_price - self.current_price) * self.size_coins

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.side == "LONG":
            return (self.current_price - self.entry_price) / self.entry_price * 100
        return (self.entry_price - self.current_price) / self.entry_price * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "coin": self.coin, "side": self.side,
            "entry_price": self.entry_price, "current_price": self.current_price,
            "size_coins": self.size_coins, "size_usd": self.size_usd,
            "sl_price": round(self.sl_price, 6), "tp_price": round(self.tp_price, 6),
            "unrealized_pnl": round(self.unrealized_pnl, 4),
            "pnl_pct": round(self.pnl_pct, 4),
            "hold_sec": round(self.hold_sec),
            "hwm": round(self.high_water_mark, 6),
            "partial_closed": self.partial_closed,
            "regime": self.regime, "entry_tag": self.entry_tag,
        }


class PositionManager:
    """Manages all open and closed positions."""

    def __init__(self, taker_fee: float = 0.0005) -> None:
        self.open_positions: list[Position] = []
        self.closed_positions: list[Position] = []
        self._taker_fee = taker_fee

    def open(self, coin: str, side: str, entry_price: float, size_coins: float,
             size_usd: float, leverage: int, risk_levels: RiskLevels,
             signal_score: float = 0.0, trend_score: float = 0.0,
             micro_score: float = 0.0, regime: str = "", entry_tag: str = "") -> Position:
        """Open a new position."""
        pos = Position(
            coin=coin, side=side, entry_price=entry_price,
            size_coins=size_coins, size_usd=size_usd, leverage=leverage,
            sl_price=risk_levels.sl_price, tp_price=risk_levels.tp_price,
            trailing_activation_pct=risk_levels.trailing_activation_pct,
            trailing_distance_pct=risk_levels.trailing_distance_pct,
            current_price=entry_price,
            high_water_mark=entry_price, low_water_mark=entry_price,
            signal_score=signal_score, trend_score=trend_score,
            micro_score=micro_score, regime=regime, entry_tag=entry_tag,
        )
        self.open_positions.append(pos)
        logger.info("position.opened", coin=coin, side=side, entry=entry_price, size=size_coins)
        return pos

    def update_price(self, coin: str, mid: float) -> list[Position]:
        """Update price and return positions that should be closed."""
        to_close = []
        for pos in self.open_positions:
            if pos.coin != coin:
                continue
            pos.current_price = mid

            # Update HWM / LWM
            if mid > pos.high_water_mark:
                pos.high_water_mark = mid
            if mid < pos.low_water_mark:
                pos.low_water_mark = mid

            # Trailing stop logic
            if pos.pnl_pct >= pos.trailing_activation_pct:
                if pos.side == "LONG":
                    new_sl = pos.high_water_mark * (1 - pos.trailing_distance_pct / 100)
                    if new_sl > pos.sl_price:
                        pos.sl_price = new_sl
                else:
                    new_sl = pos.low_water_mark * (1 + pos.trailing_distance_pct / 100)
                    if new_sl < pos.sl_price:
                        pos.sl_price = new_sl

            # Check SL
            if pos.side == "LONG" and mid <= pos.sl_price:
                pos.exit_reason = "SL"
                to_close.append(pos)
            elif pos.side == "SHORT" and mid >= pos.sl_price:
                pos.exit_reason = "SL"
                to_close.append(pos)
            # Check TP
            elif pos.side == "LONG" and mid >= pos.tp_price:
                pos.exit_reason = "TP"
                to_close.append(pos)
            elif pos.side == "SHORT" and mid <= pos.tp_price:
                pos.exit_reason = "TP"
                to_close.append(pos)

        return to_close

    def close(self, pos: Position, exit_price: float, reason: str = "") -> dict[str, Any]:
        """Close a position and calculate final PnL."""
        pos.exit_price = exit_price
        pos.exit_time = time.time()
        pos.exit_reason = reason or pos.exit_reason

        if pos.side == "LONG":
            gross_pnl = (exit_price - pos.entry_price) * pos.size_coins
        else:
            gross_pnl = (pos.entry_price - exit_price) * pos.size_coins

        entry_fee = pos.entry_price * pos.size_coins * self._taker_fee
        exit_fee = exit_price * pos.size_coins * self._taker_fee
        net_pnl = gross_pnl - entry_fee - exit_fee + pos.partial_pnl

        self.open_positions.remove(pos)
        self.closed_positions.append(pos)

        result = {
            "coin": pos.coin, "side": pos.side, "reason": pos.exit_reason,
            "entry": pos.entry_price, "exit": exit_price,
            "gross_pnl": round(gross_pnl, 4),
            "fees": round(entry_fee + exit_fee, 4),
            "net_pnl": round(net_pnl, 4),
            "hold_sec": round(pos.hold_sec),
        }
        logger.info("position.closed", **result)
        return result

    def get_position(self, coin: str) -> Position | None:
        for pos in self.open_positions:
            if pos.coin == coin:
                return pos
        return None

    @property
    def total_exposure_usd(self) -> float:
        return sum(p.size_usd for p in self.open_positions)

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.open_positions)
