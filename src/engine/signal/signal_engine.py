"""Signal Engine: detects trade entry opportunities.

Combines trend, momentum, and microstructure features into a unified
signal score. Does NOT decide whether to trade — that's the Policy Engine's job.
"""

from dataclasses import dataclass
from typing import Any

from ...features.store import FeatureStore
from ...features.base import FeatureSnapshot
from ...regime.detector import RegimeDetector, Regime


@dataclass(slots=True)
class Signal:
    """A detected trading opportunity."""
    coin: str
    side: str               # LONG or SHORT
    score: float             # composite signal strength [0, 1]
    trend_score: float       # trend component [0, 1]
    micro_score: float       # microstructure component [0, 1]
    momentum_score: float    # momentum component [0, 1]
    regime: Regime
    features: dict[str, float]

    @property
    def is_long(self) -> bool:
        return self.side == "LONG"

    def to_dict(self) -> dict[str, Any]:
        return {
            "coin": self.coin,
            "side": self.side,
            "score": round(self.score, 4),
            "trend_score": round(self.trend_score, 4),
            "micro_score": round(self.micro_score, 4),
            "momentum_score": round(self.momentum_score, 4),
            "regime": self.regime.value,
        }


class SignalEngine:
    """Scans coins for entry signals based on feature snapshots."""

    def __init__(self, feature_store: FeatureStore, regime_detector: RegimeDetector) -> None:
        self._fs = feature_store
        self._regime = regime_detector

    def scan(self, coins: list[str], min_score: float = 0.3) -> list[Signal]:
        """Scan all coins and return signals above min_score."""
        signals = []
        for coin in coins:
            for side in ["LONG", "SHORT"]:
                signal = self._evaluate(coin, side)
                if signal and signal.score >= min_score:
                    signals.append(signal)

        # Sort by score descending
        signals.sort(key=lambda s: s.score, reverse=True)
        return signals

    def _evaluate(self, coin: str, side: str) -> Signal | None:
        snap = self._fs.get_snapshot(coin)
        regime_state = self._regime.detect(coin)
        f = snap.features

        trend = self._trend_score(f, side)
        micro = self._micro_score(f, side)
        momentum = self._momentum_score(f, side)

        # Weighted composite — trend is king, micro confirms, momentum adds edge
        score = trend * 0.45 + micro * 0.30 + momentum * 0.25

        # Penalize signals against the regime
        if regime_state.regime == Regime.TRENDING_UP and side == "SHORT":
            score *= 0.3
        elif regime_state.regime == Regime.TRENDING_DOWN and side == "LONG":
            score *= 0.3
        elif regime_state.regime == Regime.QUIET:
            score *= 0.5  # less edge in quiet markets
        elif regime_state.regime == Regime.HIGH_VOL:
            score *= 0.7  # risky but can be profitable

        if score < 0.1:
            return None

        return Signal(
            coin=coin,
            side=side,
            score=round(score, 4),
            trend_score=round(trend, 4),
            micro_score=round(micro, 4),
            momentum_score=round(momentum, 4),
            regime=regime_state.regime,
            features=f,
        )

    def _trend_score(self, f: dict, side: str) -> float:
        """Score trend alignment [0, 1]."""
        strength = f.get("trend_strength", 0.0)
        r2 = f.get("trend_r2_5m", 0.0)
        consistency = f.get("trend_consistency", 0.5)
        slope = f.get("trend_slope_5m", 0.0)

        # Direction alignment
        if side == "LONG":
            direction = max(strength, 0)
            slope_aligned = max(slope, 0)
        else:
            direction = max(-strength, 0)
            slope_aligned = max(-slope, 0)

        # Composite: direction * quality
        raw = direction * (0.5 + r2 * 0.3 + (consistency - 0.5) * 0.4)
        return min(max(raw, 0.0), 1.0)

    def _micro_score(self, f: dict, side: str) -> float:
        """Score microstructure alignment [0, 1]."""
        imbalance = f.get("micro_imbalance", 0.0)
        trade_imb = f.get("micro_trade_imbalance", 0.0)
        depth_ratio = f.get("micro_depth_ratio", 1.0)
        spread = f.get("micro_spread_bps", 0.0)

        # Direction alignment
        if side == "LONG":
            ob_signal = max(imbalance, 0) * 0.5 + max(trade_imb, 0) * 0.5
        else:
            ob_signal = max(-imbalance, 0) * 0.5 + max(-trade_imb, 0) * 0.5

        # Tight spread = better execution
        spread_bonus = max(0, 1 - spread / 10) * 0.2

        raw = ob_signal + spread_bonus
        return min(max(raw, 0.0), 1.0)

    def _momentum_score(self, f: dict, side: str) -> float:
        """Score momentum alignment [0, 1]."""
        ret_2m = f.get("mom_ret_2m", 0.0)
        ret_5m = f.get("mom_ret_5m", 0.0)
        accel = f.get("mom_acceleration", 0.0)
        buy_pressure = f.get("mom_buy_pressure", 0.5)

        if side == "LONG":
            mom = max(ret_2m, 0) * 0.4 + max(ret_5m, 0) * 0.2 + max(accel, 0) * 0.2
            mom += (buy_pressure - 0.5) * 0.4
        else:
            mom = max(-ret_2m, 0) * 0.4 + max(-ret_5m, 0) * 0.2 + max(-accel, 0) * 0.2
            mom += (0.5 - buy_pressure) * 0.4

        return min(max(mom, 0.0), 1.0)
