"""Momentum features: price change rates over multiple windows.

Captures directional strength and acceleration of price movement.
"""

import numpy as np
from ..base import FeatureComputer

WINDOWS_SEC = [60, 120, 300, 600, 1800]  # 1m, 2m, 5m, 10m, 30m


class MomentumFeatures(FeatureComputer):
    name = "momentum"
    version = "v1"

    @property
    def feature_names(self) -> list[str]:
        names = []
        for w in WINDOWS_SEC:
            tag = f"{w // 60}m"
            names.extend([f"mom_ret_{tag}", f"mom_vwap_ret_{tag}", f"mom_trades_{tag}"])
        names.extend(["mom_acceleration", "mom_buy_pressure"])
        return names

    def compute(self, coin: str, trades: list[dict], books: list[dict]) -> dict[str, float]:
        if not trades:
            return {n: 0.0 for n in self.feature_names}

        now_ms = trades[-1].get("timestamp_ms", 0)
        features = {}

        for w in WINDOWS_SEC:
            tag = f"{w // 60}m"
            cutoff = now_ms - w * 1000
            window_trades = [t for t in trades if t.get("timestamp_ms", 0) >= cutoff]

            if len(window_trades) < 2:
                features[f"mom_ret_{tag}"] = 0.0
                features[f"mom_vwap_ret_{tag}"] = 0.0
                features[f"mom_trades_{tag}"] = 0.0
                continue

            first_px = window_trades[0]["price"]
            last_px = window_trades[-1]["price"]
            ret = (last_px - first_px) / first_px * 100 if first_px > 0 else 0.0
            features[f"mom_ret_{tag}"] = round(ret, 6)

            # VWAP return
            prices = np.array([t["price"] for t in window_trades])
            sizes = np.array([t["size"] for t in window_trades])
            total_vol = sizes.sum()
            if total_vol > 0:
                vwap = (prices * sizes).sum() / total_vol
                vwap_ret = (last_px - vwap) / vwap * 100
                features[f"mom_vwap_ret_{tag}"] = round(vwap_ret, 6)
            else:
                features[f"mom_vwap_ret_{tag}"] = 0.0

            features[f"mom_trades_{tag}"] = float(len(window_trades))

        # Acceleration: 2m return - 5m return (positive = accelerating)
        ret_2m = features.get("mom_ret_2m", 0.0)
        ret_5m = features.get("mom_ret_5m", 0.0)
        features["mom_acceleration"] = round(ret_2m - ret_5m / 2.5, 6)

        # Buy pressure: % of volume that is buys in last 2 min
        cutoff_2m = now_ms - 120_000
        recent = [t for t in trades if t.get("timestamp_ms", 0) >= cutoff_2m]
        buy_vol = sum(t["size"] for t in recent if t.get("side") == "BUY")
        total_vol = sum(t["size"] for t in recent)
        features["mom_buy_pressure"] = round(buy_vol / total_vol, 4) if total_vol > 0 else 0.5

        return features
