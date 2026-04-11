"""Market Regime Detection Engine.

Classifies current market conditions into regimes that drive policy decisions.
Uses features from the Feature Store — no raw data access needed.

Regimes:
  TRENDING_UP    — strong directional move up, high R², consistent
  TRENDING_DOWN  — strong directional move down, high R², consistent
  CHOPPY         — no direction, low R², mean-reverting
  HIGH_VOL       — elevated volatility regardless of direction
  QUIET          — low volatility, tight spreads, minimal activity
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

from ..features.store import FeatureStore
from ..features.base import FeatureSnapshot

logger = structlog.get_logger()


class Regime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    CHOPPY = "choppy"
    HIGH_VOL = "high_vol"
    QUIET = "quiet"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class RegimeState:
    """Current regime assessment for a coin."""
    coin: str
    regime: Regime
    confidence: float       # 0-1, how confident we are in this classification
    trend_strength: float   # -1 to 1
    volatility_level: float # normalized vol (0 = dead, 1+ = extreme)
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "coin": self.coin,
            "regime": self.regime.value,
            "confidence": self.confidence,
            "trend_strength": self.trend_strength,
            "volatility_level": self.volatility_level,
            **self.details,
        }


# Thresholds for regime classification
TREND_STRONG = 0.35       # trend_strength above this = trending
TREND_WEAK = 0.12         # below this = no trend
VOL_HIGH = 0.15           # vol_realized_5m above this = high vol
VOL_QUIET = 0.02          # below this = quiet
R2_CLEAN = 0.5            # R² above this = clean trend
CONSISTENCY_HIGH = 0.65   # consistency above this = directional


class RegimeDetector:
    """Detects market regime per coin using Feature Store snapshots."""

    def __init__(self, feature_store: FeatureStore) -> None:
        self._fs = feature_store
        self._history: dict[str, list[Regime]] = {}  # last N regimes per coin for persistence
        self._max_history = 20

    def detect(self, coin: str) -> RegimeState:
        """Classify current regime for a coin."""
        snapshot = self._fs.get_snapshot(coin)
        return self._classify(coin, snapshot)

    def detect_all(self, coins: list[str] | None = None) -> dict[str, RegimeState]:
        """Classify regime for all coins."""
        snapshots = self._fs.get_all_snapshots(coins)
        return {coin: self._classify(coin, snap) for coin, snap in snapshots.items()}

    def _classify(self, coin: str, snap: FeatureSnapshot) -> RegimeState:
        f = snap.features

        # Extract key features
        trend_strength = f.get("trend_strength", 0.0)
        vol_5m = f.get("vol_realized_5m", 0.0)
        r2 = f.get("trend_r2_5m", 0.0)
        consistency = f.get("trend_consistency", 0.5)
        slope_5m = f.get("trend_slope_5m", 0.0)
        vol_ratio = f.get("vol_ratio_1m_5m", 1.0)
        spread = f.get("micro_spread_bps", 0.0)
        intensity = f.get("micro_intensity", 0.0)

        # Normalize volatility to a 0-1+ scale
        vol_level = min(vol_5m / VOL_HIGH, 2.0) if VOL_HIGH > 0 else 0.0

        regime = Regime.UNKNOWN
        confidence = 0.0

        # Priority 1: HIGH_VOL — supersedes trend if vol is extreme
        if vol_5m > VOL_HIGH and vol_ratio > 1.5:
            regime = Regime.HIGH_VOL
            confidence = min(vol_5m / VOL_HIGH / 2, 1.0)

        # Priority 2: TRENDING — strong direction with clean structure
        elif abs(trend_strength) > TREND_STRONG and r2 > R2_CLEAN:
            if trend_strength > 0:
                regime = Regime.TRENDING_UP
            else:
                regime = Regime.TRENDING_DOWN
            confidence = min(abs(trend_strength) * r2 * 2, 1.0)

        # Priority 3: Moderate trend — directional but less clean
        elif abs(trend_strength) > TREND_WEAK and consistency > CONSISTENCY_HIGH:
            if trend_strength > 0:
                regime = Regime.TRENDING_UP
            else:
                regime = Regime.TRENDING_DOWN
            confidence = min(abs(trend_strength) * consistency, 0.7)

        # Priority 4: QUIET — very low vol and activity
        elif vol_5m < VOL_QUIET and intensity < 1.0:
            regime = Regime.QUIET
            confidence = min((VOL_QUIET - vol_5m) / VOL_QUIET + 0.3, 1.0)

        # Default: CHOPPY
        else:
            regime = Regime.CHOPPY
            # Confidence increases when vol is moderate but no direction
            confidence = min(0.3 + (1 - abs(trend_strength)) * 0.5, 0.8)

        # Apply persistence: don't flip regime on single tick
        regime = self._apply_persistence(coin, regime)

        state = RegimeState(
            coin=coin,
            regime=regime,
            confidence=round(confidence, 3),
            trend_strength=round(trend_strength, 4),
            volatility_level=round(vol_level, 4),
            details={
                "vol_5m": round(vol_5m, 6),
                "r2": round(r2, 4),
                "consistency": round(consistency, 4),
                "slope_5m": round(slope_5m, 6),
                "vol_ratio": round(vol_ratio, 4),
                "spread_bps": round(spread, 2),
                "intensity": round(intensity, 4),
            },
        )

        return state

    def _apply_persistence(self, coin: str, new_regime: Regime) -> Regime:
        """Require 3 consecutive same-regime readings before switching.

        Prevents noise-driven regime flips.
        """
        history = self._history.setdefault(coin, [])
        history.append(new_regime)
        if len(history) > self._max_history:
            history.pop(0)

        # Need at least 3 readings of the same regime to confirm switch
        if len(history) >= 3 and history[-1] == history[-2] == history[-3]:
            return new_regime

        # Otherwise keep previous confirmed regime
        # Find the last confirmed regime (3 consecutive)
        for i in range(len(history) - 3, -1, -1):
            if history[i] == history[i + 1] == history[i + 2]:
                return history[i]

        # No confirmed regime yet — use current reading
        return new_regime

    def get_regime_summary(self, coins: list[str]) -> dict[str, int]:
        """Count how many coins are in each regime."""
        states = self.detect_all(coins)
        summary: dict[str, int] = {}
        for state in states.values():
            r = state.regime.value
            summary[r] = summary.get(r, 0) + 1
        return summary
