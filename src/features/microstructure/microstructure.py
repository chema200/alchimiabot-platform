"""Microstructure features: orderbook imbalance, spread, depth, trade flow.

Captures the fine-grained market structure that drives short-term price impact.
"""

import numpy as np
from ..base import FeatureComputer


class MicrostructureFeatures(FeatureComputer):
    name = "microstructure"
    version = "v1"

    @property
    def feature_names(self) -> list[str]:
        return [
            "micro_spread_bps",         # current bid-ask spread in bps
            "micro_bid_depth",          # total bid size (top 5 levels)
            "micro_ask_depth",          # total ask size (top 5 levels)
            "micro_imbalance",          # (bid_depth - ask_depth) / total — positive = buy pressure
            "micro_depth_ratio",        # bid_depth / ask_depth
            "micro_trade_imbalance",    # net buy volume / total volume (last 1 min)
            "micro_large_trade_ratio",  # % of volume from trades > 2x median size
            "micro_intensity",          # trades per second (last 1 min)
            "micro_vpin",               # Volume-synchronized PIN (toxicity proxy)
        ]

    def compute(self, coin: str, trades: list[dict], books: list[dict]) -> dict[str, float]:
        features = {}

        # Book features from latest snapshot
        if books:
            book = books[-1]
            bids = book.get("bids", [])
            asks = book.get("asks", [])

            if bids and asks:
                best_bid = bids[0][0]
                best_ask = asks[0][0]
                mid = (best_bid + best_ask) / 2

                features["micro_spread_bps"] = round((best_ask - best_bid) / mid * 10000, 2) if mid > 0 else 0.0

                bid_depth = sum(level[1] for level in bids[:5])
                ask_depth = sum(level[1] for level in asks[:5])
                total_depth = bid_depth + ask_depth

                features["micro_bid_depth"] = round(bid_depth, 4)
                features["micro_ask_depth"] = round(ask_depth, 4)
                features["micro_imbalance"] = round((bid_depth - ask_depth) / total_depth, 4) if total_depth > 0 else 0.0
                features["micro_depth_ratio"] = round(bid_depth / ask_depth, 4) if ask_depth > 0 else 1.0
            else:
                for k in ["micro_spread_bps", "micro_bid_depth", "micro_ask_depth", "micro_imbalance", "micro_depth_ratio"]:
                    features[k] = 0.0
        else:
            for k in ["micro_spread_bps", "micro_bid_depth", "micro_ask_depth", "micro_imbalance", "micro_depth_ratio"]:
                features[k] = 0.0

        # Trade flow features from last 1 min
        if trades:
            now_ms = trades[-1].get("timestamp_ms", 0)
            cutoff_1m = now_ms - 60_000
            recent = [t for t in trades if t.get("timestamp_ms", 0) >= cutoff_1m]

            if recent:
                buy_vol = sum(t["size"] for t in recent if t.get("side") == "BUY")
                sell_vol = sum(t["size"] for t in recent if t.get("side") == "SELL")
                total_vol = buy_vol + sell_vol

                features["micro_trade_imbalance"] = round((buy_vol - sell_vol) / total_vol, 4) if total_vol > 0 else 0.0

                # Large trade ratio
                sizes = [t["size"] for t in recent]
                if sizes:
                    median_size = float(np.median(sizes))
                    large_vol = sum(s for s in sizes if s > 2 * median_size)
                    features["micro_large_trade_ratio"] = round(large_vol / total_vol, 4) if total_vol > 0 else 0.0
                else:
                    features["micro_large_trade_ratio"] = 0.0

                # Trade intensity
                duration_sec = max((recent[-1]["timestamp_ms"] - recent[0]["timestamp_ms"]) / 1000, 1)
                features["micro_intensity"] = round(len(recent) / duration_sec, 4)

                # VPIN proxy: absolute net flow / total flow over rolling buckets
                bucket_count = min(10, len(recent))
                if bucket_count >= 3:
                    bucket_size = len(recent) // bucket_count
                    abs_imbalances = []
                    for i in range(bucket_count):
                        bucket = recent[i * bucket_size:(i + 1) * bucket_size]
                        bv = sum(t["size"] for t in bucket if t.get("side") == "BUY")
                        sv = sum(t["size"] for t in bucket if t.get("side") == "SELL")
                        tv = bv + sv
                        if tv > 0:
                            abs_imbalances.append(abs(bv - sv) / tv)
                    features["micro_vpin"] = round(float(np.mean(abs_imbalances)), 4) if abs_imbalances else 0.0
                else:
                    features["micro_vpin"] = 0.0
            else:
                for k in ["micro_trade_imbalance", "micro_large_trade_ratio", "micro_intensity", "micro_vpin"]:
                    features[k] = 0.0
        else:
            for k in ["micro_trade_imbalance", "micro_large_trade_ratio", "micro_intensity", "micro_vpin"]:
                features[k] = 0.0

        return features
