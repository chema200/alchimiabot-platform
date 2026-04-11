"""Regime performance evaluator.

Tracks how each regime performs over time — which regimes are profitable
for which strategies. Feeds into the policy engine for regime-aware decisions.
"""

from dataclasses import dataclass, field
from collections import defaultdict
from typing import Any

from .detector import Regime


@dataclass
class RegimeStats:
    """Performance statistics for a specific regime."""
    trades: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    avg_hold_sec: float = 0.0
    _hold_sum: float = field(default=0.0, repr=False)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades > 0 else 0.0

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.trades if self.trades > 0 else 0.0

    @property
    def expectancy(self) -> float:
        """Expected PnL per trade."""
        return self.avg_pnl

    def record(self, pnl: float, fee: float, hold_sec: float, won: bool) -> None:
        self.trades += 1
        if won:
            self.wins += 1
        self.total_pnl += pnl
        self.total_fees += fee
        self._hold_sum += hold_sec
        self.avg_hold_sec = self._hold_sum / self.trades

    def to_dict(self) -> dict[str, Any]:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "win_rate": round(self.win_rate, 4),
            "total_pnl": round(self.total_pnl, 4),
            "avg_pnl": round(self.avg_pnl, 4),
            "expectancy": round(self.expectancy, 4),
            "avg_hold_sec": round(self.avg_hold_sec, 1),
        }


class RegimeEvaluator:
    """Tracks performance by regime, coin, and regime+coin."""

    def __init__(self) -> None:
        self._by_regime: dict[Regime, RegimeStats] = defaultdict(RegimeStats)
        self._by_coin_regime: dict[str, dict[Regime, RegimeStats]] = defaultdict(lambda: defaultdict(RegimeStats))

    def record_trade(self, coin: str, regime: Regime, pnl: float, fee: float,
                     hold_sec: float, won: bool) -> None:
        """Record a completed trade's performance under its regime."""
        self._by_regime[regime].record(pnl, fee, hold_sec, won)
        self._by_coin_regime[coin][regime].record(pnl, fee, hold_sec, won)

    def get_regime_stats(self, regime: Regime) -> RegimeStats:
        return self._by_regime.get(regime, RegimeStats())

    def get_coin_regime_stats(self, coin: str, regime: Regime) -> RegimeStats:
        return self._by_coin_regime.get(coin, {}).get(regime, RegimeStats())

    def should_trade_regime(self, regime: Regime, min_trades: int = 10) -> bool:
        """Quick check: is this regime historically profitable?"""
        stats = self._by_regime.get(regime)
        if not stats or stats.trades < min_trades:
            return True  # not enough data, allow trading
        return stats.expectancy > 0

    def get_full_report(self) -> dict[str, Any]:
        return {
            "by_regime": {r.value: s.to_dict() for r, s in self._by_regime.items()},
            "by_coin_regime": {
                coin: {r.value: s.to_dict() for r, s in regimes.items()}
                for coin, regimes in self._by_coin_regime.items()
            },
        }
