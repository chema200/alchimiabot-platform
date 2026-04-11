"""Trend features: direction, strength, and consistency of price movement.

Distinguishes between trending and mean-reverting markets.
"""

import numpy as np
from ..base import FeatureComputer


class TrendFeatures(FeatureComputer):
    name = "trend"
    version = "v1"

    @property
    def feature_names(self) -> list[str]:
        return [
            "trend_ema_cross",      # fast EMA vs slow EMA (positive = bullish)
            "trend_slope_2m",       # linear regression slope over 2 min
            "trend_slope_5m",       # linear regression slope over 5 min
            "trend_r2_5m",          # R² of regression — how clean the trend is
            "trend_consistency",    # % of 10s intervals that moved in same direction
            "trend_higher_highs",   # count of higher highs in last 5 min
            "trend_strength",       # composite trend score [-1, 1]
        ]

    def compute(self, coin: str, trades: list[dict], books: list[dict]) -> dict[str, float]:
        if len(trades) < 10:
            return {n: 0.0 for n in self.feature_names}

        now_ms = trades[-1].get("timestamp_ms", 0)
        features = {}

        # EMA cross: 20-trade EMA vs 50-trade EMA
        prices = np.array([t["price"] for t in trades[-100:]])
        ema_fast = self._ema(prices, min(20, len(prices)))
        ema_slow = self._ema(prices, min(50, len(prices)))
        if ema_slow > 0:
            features["trend_ema_cross"] = round((ema_fast - ema_slow) / ema_slow * 100, 6)
        else:
            features["trend_ema_cross"] = 0.0

        # Linear regression slopes
        for window_sec, tag in [(120, "2m"), (300, "5m")]:
            cutoff = now_ms - window_sec * 1000
            window_trades = [(t["timestamp_ms"], t["price"]) for t in trades
                           if t.get("timestamp_ms", 0) >= cutoff]

            if len(window_trades) < 5:
                features[f"trend_slope_{tag}"] = 0.0
                if tag == "5m":
                    features["trend_r2_5m"] = 0.0
                continue

            times = np.array([w[0] for w in window_trades], dtype=float)
            px = np.array([w[1] for w in window_trades])
            times -= times[0]  # normalize

            # Linear regression
            coeffs = np.polyfit(times, px, 1)
            slope_per_sec = coeffs[0] * 1000  # per second
            mid = px.mean()
            slope_pct = (slope_per_sec / mid * 100) if mid > 0 else 0.0
            features[f"trend_slope_{tag}"] = round(slope_pct, 6)

            if tag == "5m":
                # R² for trend quality
                predicted = np.polyval(coeffs, times)
                ss_res = np.sum((px - predicted) ** 2)
                ss_tot = np.sum((px - px.mean()) ** 2)
                r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
                features["trend_r2_5m"] = round(max(0, r2), 4)

        # Consistency: % of 10s intervals moving in the dominant direction
        cutoff_5m = now_ms - 300_000
        window_prices = [t["price"] for t in trades if t.get("timestamp_ms", 0) >= cutoff_5m]
        if len(window_prices) >= 10:
            # Sample at regular intervals
            sampled = window_prices[::max(1, len(window_prices) // 30)]
            diffs = np.diff(sampled)
            if len(diffs) > 0:
                ups = np.sum(diffs > 0)
                consistency = max(ups, len(diffs) - ups) / len(diffs)
                features["trend_consistency"] = round(float(consistency), 4)
            else:
                features["trend_consistency"] = 0.5
        else:
            features["trend_consistency"] = 0.5

        # Higher highs count (bullish structure)
        if len(window_prices) >= 20:
            chunks = np.array_split(window_prices, min(10, len(window_prices) // 2))
            highs = [max(c) for c in chunks if len(c) > 0]
            hh_count = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
            features["trend_higher_highs"] = float(hh_count)
        else:
            features["trend_higher_highs"] = 0.0

        # Composite trend strength [-1, 1]
        slope = features.get("trend_slope_5m", 0.0)
        r2 = features.get("trend_r2_5m", 0.0)
        ema = features.get("trend_ema_cross", 0.0)
        raw_strength = np.tanh(slope * 5) * r2 * 0.5 + np.tanh(ema * 3) * 0.5
        features["trend_strength"] = round(float(np.clip(raw_strength, -1, 1)), 4)

        return features

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> float:
        if len(data) == 0:
            return 0.0
        alpha = 2 / (period + 1)
        ema = data[0]
        for px in data[1:]:
            ema = alpha * px + (1 - alpha) * ema
        return float(ema)
