"""Volatility features: realized vol, ATR, range metrics.

Measures how much and how fast price is moving — critical for
sizing, SL placement, and regime detection.
"""

import numpy as np
from ..base import FeatureComputer


class VolatilityFeatures(FeatureComputer):
    name = "volatility"
    version = "v1"

    @property
    def feature_names(self) -> list[str]:
        return [
            "vol_realized_1m", "vol_realized_5m", "vol_realized_30m",
            "vol_range_1m", "vol_range_5m",
            "vol_atr_5m",
            "vol_ratio_1m_5m",  # short vol / long vol — spikes when vol increases
            "vol_parkinson",    # Parkinson estimator (high-low based)
        ]

    def compute(self, coin: str, trades: list[dict], books: list[dict]) -> dict[str, float]:
        if len(trades) < 5:
            return {n: 0.0 for n in self.feature_names}

        now_ms = trades[-1].get("timestamp_ms", 0)
        features = {}

        # Realized volatility (std of log returns) over windows
        for window_sec, tag in [(60, "1m"), (300, "5m"), (1800, "30m")]:
            cutoff = now_ms - window_sec * 1000
            window_prices = [t["price"] for t in trades if t.get("timestamp_ms", 0) >= cutoff]

            if len(window_prices) < 3:
                features[f"vol_realized_{tag}"] = 0.0
                continue

            prices = np.array(window_prices)
            log_returns = np.diff(np.log(prices))
            features[f"vol_realized_{tag}"] = round(float(np.std(log_returns) * 100), 6)

        # Range (high-low / mid) over windows
        for window_sec, tag in [(60, "1m"), (300, "5m")]:
            cutoff = now_ms - window_sec * 1000
            window_prices = [t["price"] for t in trades if t.get("timestamp_ms", 0) >= cutoff]

            if len(window_prices) < 2:
                features[f"vol_range_{tag}"] = 0.0
                continue

            high = max(window_prices)
            low = min(window_prices)
            mid = (high + low) / 2
            features[f"vol_range_{tag}"] = round((high - low) / mid * 100, 6) if mid > 0 else 0.0

        # ATR proxy: average of 1-min ranges over last 5 min
        cutoff_5m = now_ms - 300_000
        minute_ranges = []
        for i in range(5):
            start = cutoff_5m + i * 60_000
            end = start + 60_000
            minute_prices = [t["price"] for t in trades
                           if start <= t.get("timestamp_ms", 0) < end]
            if len(minute_prices) >= 2:
                minute_ranges.append(max(minute_prices) - min(minute_prices))

        if minute_ranges:
            atr = np.mean(minute_ranges)
            mid = trades[-1]["price"]
            features["vol_atr_5m"] = round(atr / mid * 100, 6) if mid > 0 else 0.0
        else:
            features["vol_atr_5m"] = 0.0

        # Vol ratio: short-term / long-term (> 1 = vol expanding)
        vol_1m = features.get("vol_realized_1m", 0.0)
        vol_5m = features.get("vol_realized_5m", 0.0)
        features["vol_ratio_1m_5m"] = round(vol_1m / vol_5m, 4) if vol_5m > 0 else 1.0

        # Parkinson volatility estimator (more efficient than close-to-close)
        cutoff_5m = now_ms - 300_000
        prices_5m = [t["price"] for t in trades if t.get("timestamp_ms", 0) >= cutoff_5m]
        if len(prices_5m) >= 5:
            high = max(prices_5m)
            low = min(prices_5m)
            if low > 0:
                features["vol_parkinson"] = round(np.log(high / low) ** 2 / (4 * np.log(2)) * 100, 6)
            else:
                features["vol_parkinson"] = 0.0
        else:
            features["vol_parkinson"] = 0.0

        return features
