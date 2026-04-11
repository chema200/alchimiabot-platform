"""Execution Simulator: models realistic order execution.

Simulates what happens when you send an order to the exchange:
slippage, partial fills, fees, latency. Used for backtesting
and replay so results match live trading.
"""

from dataclasses import dataclass
from typing import Any
import random


@dataclass(slots=True)
class ExecutionResult:
    """Result of a simulated order execution."""
    filled: bool
    fill_price: float
    fill_size: float
    slippage_bps: float
    fee: float
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "filled": self.filled,
            "fill_price": round(self.fill_price, 6),
            "fill_size": round(self.fill_size, 6),
            "slippage_bps": round(self.slippage_bps, 2),
            "fee": round(self.fee, 6),
            "latency_ms": round(self.latency_ms, 1),
        }


@dataclass
class ExecutionConfig:
    taker_fee_bps: float = 5.0         # 0.05%
    maker_fee_bps: float = -2.0        # -0.02% (rebate)
    avg_slippage_bps: float = 1.0      # average slippage
    max_slippage_bps: float = 5.0      # worst case
    avg_latency_ms: float = 50.0
    latency_std_ms: float = 20.0
    partial_fill_probability: float = 0.05  # 5% chance of partial fill
    fill_rate: float = 0.98            # 98% of IOC orders fill


class ExecutionSimulator:
    """Simulates order execution for backtesting and replay."""

    def __init__(self, config: ExecutionConfig | None = None) -> None:
        self._config = config or ExecutionConfig()

    def simulate_ioc(self, coin: str, is_buy: bool, price: float, size: float,
                     spread_bps: float = 3.0, book_depth: float = 1.0) -> ExecutionResult:
        """Simulate an IOC (Immediate-or-Cancel) market order."""
        cfg = self._config

        # Fill probability
        if random.random() > cfg.fill_rate:
            return ExecutionResult(False, 0.0, 0.0, 0.0, 0.0, 0.0)

        # Slippage: depends on spread and our size vs book depth
        size_impact = min(size / max(book_depth, 0.01), 3.0)  # cap at 3x depth
        base_slip = cfg.avg_slippage_bps + spread_bps * 0.1
        slippage_bps = base_slip * (1 + size_impact * 0.3)
        slippage_bps = min(slippage_bps, cfg.max_slippage_bps)
        # Add randomness
        slippage_bps *= (0.5 + random.random())

        # Apply slippage to price
        slip_mult = slippage_bps / 10000
        if is_buy:
            fill_price = price * (1 + slip_mult)
        else:
            fill_price = price * (1 - slip_mult)

        # Partial fill
        if random.random() < cfg.partial_fill_probability:
            fill_size = size * (0.5 + random.random() * 0.4)
        else:
            fill_size = size

        # Fee (taker for IOC)
        fee = fill_price * fill_size * cfg.taker_fee_bps / 10000

        # Latency
        latency = max(10, random.gauss(cfg.avg_latency_ms, cfg.latency_std_ms))

        return ExecutionResult(
            filled=True,
            fill_price=fill_price,
            fill_size=fill_size,
            slippage_bps=slippage_bps,
            fee=fee,
            latency_ms=latency,
        )

    def simulate_maker(self, coin: str, is_buy: bool, price: float, size: float,
                       time_in_book_sec: float = 5.0) -> ExecutionResult:
        """Simulate a maker/limit order."""
        cfg = self._config

        # Maker orders have better pricing but may not fill
        # Fill probability decreases with distance from mid and time
        fill_prob = min(0.3 + time_in_book_sec * 0.05, 0.85)
        if random.random() > fill_prob:
            return ExecutionResult(False, 0.0, 0.0, 0.0, 0.0, 0.0)

        # No slippage for maker (you set the price)
        fill_price = price
        fill_size = size

        # Maker fee (negative = rebate)
        fee = fill_price * fill_size * cfg.maker_fee_bps / 10000

        latency = max(50, random.gauss(cfg.avg_latency_ms * 2, cfg.latency_std_ms))

        return ExecutionResult(
            filled=True,
            fill_price=fill_price,
            fill_size=fill_size,
            slippage_bps=0.0,
            fee=fee,
            latency_ms=latency,
        )
