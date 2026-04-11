"""Sizing Engine: determines position size based on signal, regime, and risk.

Outputs a USD notional and leverage-adjusted size in coin units.
"""

from dataclasses import dataclass

from ..policy.policy_engine import PolicyDecision
from ...features.base import FeatureSnapshot


@dataclass
class SizeResult:
    coin: str
    side: str
    size_usd: float          # notional in USD
    size_coins: float        # quantity in coin units
    leverage: int
    risk_pct: float          # % of budget risked

    def to_dict(self) -> dict:
        return {
            "coin": self.coin,
            "side": self.side,
            "size_usd": round(self.size_usd, 2),
            "size_coins": round(self.size_coins, 6),
            "leverage": self.leverage,
            "risk_pct": round(self.risk_pct, 4),
        }


class SizingEngine:
    """Calculates position size."""

    def __init__(self, budget: float, default_leverage: int = 3,
                 base_size_pct: float = 2.5, max_size_usd: float = 150.0,
                 min_size_usd: float = 15.0) -> None:
        self._budget = budget
        self._leverage = default_leverage
        self._base_pct = base_size_pct
        self._max_usd = max_size_usd
        self._min_usd = min_size_usd

    def calculate(self, decision: PolicyDecision, price: float) -> SizeResult | None:
        """Calculate position size for an accepted signal."""
        if decision.action != "ENTER" or price <= 0:
            return None

        # Base size: % of budget
        base_usd = self._budget * self._base_pct / 100

        # Scale by signal score (stronger signal = larger size)
        score_mult = 0.5 + decision.adjusted_score  # range: 0.5 to ~1.5

        # Apply policy multiplier (regime boost/reduce)
        size_usd = base_usd * score_mult * decision.size_multiplier

        # Clamp
        size_usd = max(self._min_usd, min(size_usd, self._max_usd))

        # Leverage-adjusted coin quantity
        size_coins = (size_usd * self._leverage) / price
        risk_pct = size_usd / self._budget * 100

        return SizeResult(
            coin=decision.signal.coin,
            side=decision.signal.side,
            size_usd=size_usd,
            size_coins=size_coins,
            leverage=self._leverage,
            risk_pct=risk_pct,
        )

    def update_budget(self, new_budget: float) -> None:
        self._budget = new_budget
