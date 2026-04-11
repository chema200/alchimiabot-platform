"""Risk Manager: SL/TP calculation, drawdown limits, portfolio constraints.

Computes exit levels based on ATR, regime, and position context.
Monitors portfolio-level risk and can force close positions.
"""

from dataclasses import dataclass, field
from typing import Any
import time

import structlog

from ...features.base import FeatureSnapshot
from ...regime.detector import Regime

logger = structlog.get_logger()


@dataclass
class RiskLevels:
    """Calculated exit levels for a position."""
    sl_price: float
    tp_price: float
    sl_pct: float
    tp_pct: float
    trailing_activation_pct: float
    trailing_distance_pct: float

    def to_dict(self) -> dict:
        return {
            "sl_price": round(self.sl_price, 6),
            "tp_price": round(self.tp_price, 6),
            "sl_pct": round(self.sl_pct, 4),
            "tp_pct": round(self.tp_pct, 4),
            "trailing_activation_pct": round(self.trailing_activation_pct, 4),
            "trailing_distance_pct": round(self.trailing_distance_pct, 4),
        }


@dataclass
class RiskConfig:
    base_sl_pct: float = 0.5
    base_tp_pct: float = 1.2
    atr_sl_multiplier: float = 1.5
    atr_tp_multiplier: float = 2.5
    atr_sl_min_pct: float = 0.15
    atr_sl_max_pct: float = 1.0
    atr_tp_min_pct: float = 0.3
    atr_tp_max_pct: float = 2.0
    trailing_activation_pct: float = 0.4
    trailing_distance_pct: float = 0.3
    max_portfolio_drawdown_pct: float = 5.0
    max_daily_loss_usd: float = 50.0
    max_sl_per_window: int = 5
    sl_window_min: int = 30


class RiskManager:
    """Manages position and portfolio risk."""

    def __init__(self, config: RiskConfig) -> None:
        self._config = config
        self._daily_pnl: float = 0.0
        self._daily_reset_day: int = 0
        self._sl_timestamps: list[float] = []
        self._peak_equity: float = 0.0

    def calculate_levels(self, coin: str, side: str, entry_price: float,
                         features: dict[str, float], regime: Regime) -> RiskLevels:
        """Calculate SL/TP levels for a new position."""
        cfg = self._config
        atr_pct = features.get("vol_atr_5m", 0.0)

        # ATR-based SL
        if atr_pct > 0:
            sl_pct = atr_pct * cfg.atr_sl_multiplier
            sl_pct = max(cfg.atr_sl_min_pct, min(sl_pct, cfg.atr_sl_max_pct))
        else:
            sl_pct = cfg.base_sl_pct

        # ATR-based TP
        if atr_pct > 0:
            tp_pct = atr_pct * cfg.atr_tp_multiplier
            tp_pct = max(cfg.atr_tp_min_pct, min(tp_pct, cfg.atr_tp_max_pct))
        else:
            tp_pct = cfg.base_tp_pct

        # Regime adjustments
        if regime == Regime.HIGH_VOL:
            sl_pct *= 1.3   # wider SL in high vol
            tp_pct *= 1.5   # bigger TP target
        elif regime == Regime.QUIET:
            sl_pct *= 0.7   # tighter SL
            tp_pct *= 0.6   # smaller TP
        elif regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN):
            tp_pct *= 1.3   # let winners ride

        # Trailing config
        trail_act = cfg.trailing_activation_pct
        trail_dist = cfg.trailing_distance_pct
        if regime == Regime.HIGH_VOL:
            trail_dist *= 1.5  # wider trailing in high vol

        # Calculate price levels
        if side == "LONG":
            sl_price = entry_price * (1 - sl_pct / 100)
            tp_price = entry_price * (1 + tp_pct / 100)
        else:
            sl_price = entry_price * (1 + sl_pct / 100)
            tp_price = entry_price * (1 - tp_pct / 100)

        return RiskLevels(
            sl_price=sl_price,
            tp_price=tp_price,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            trailing_activation_pct=trail_act,
            trailing_distance_pct=trail_dist,
        )

    def record_trade_result(self, pnl: float, was_sl: bool) -> None:
        """Track trade result for portfolio risk."""
        self._daily_pnl += pnl
        if was_sl:
            self._sl_timestamps.append(time.time())
            # Trim old timestamps
            cutoff = time.time() - self._config.sl_window_min * 60
            self._sl_timestamps = [t for t in self._sl_timestamps if t > cutoff]

    def is_portfolio_risk_exceeded(self, current_equity: float) -> bool:
        """Check if portfolio-level risk limits are breached."""
        if self._peak_equity > 0:
            drawdown_pct = (self._peak_equity - current_equity) / self._peak_equity * 100
            if drawdown_pct > self._config.max_portfolio_drawdown_pct:
                return True

        if self._peak_equity < current_equity:
            self._peak_equity = current_equity

        if abs(self._daily_pnl) > self._config.max_daily_loss_usd and self._daily_pnl < 0:
            return True

        return False

    def is_sl_guard_active(self) -> bool:
        """Too many SLs in the window."""
        cutoff = time.time() - self._config.sl_window_min * 60
        recent = [t for t in self._sl_timestamps if t > cutoff]
        return len(recent) >= self._config.max_sl_per_window

    @property
    def state(self) -> dict[str, Any]:
        return {
            "daily_pnl": round(self._daily_pnl, 4),
            "peak_equity": round(self._peak_equity, 2),
            "recent_sls": len(self._sl_timestamps),
            "sl_guard_active": self.is_sl_guard_active(),
        }
