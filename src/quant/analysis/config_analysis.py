"""Config Analysis: analyze trading performance by config parameters.

Groups trades by config snapshot values (stop_loss, trailing_distance, etc.)
to identify which parameter settings produce the best results.
"""

from typing import Any

import numpy as np


class ConfigAnalyzer:
    """Analyze performance grouped by config parameters."""

    def analyze(self, trades: list[dict]) -> dict[str, Any]:
        """Analyze performance by config parameters."""
        if not trades:
            return {}

        results: dict[str, Any] = {}

        # By config_version
        results["by_config_version"] = self._group_analyze(trades, "config_version")

        # By key params (extract from config_snapshot or direct fields)
        results["by_stop_loss"] = self._bucket_analyze(
            trades, "stop_loss_pct", [0.25, 0.35, 0.4, 0.5, 0.6]
        )
        results["by_trailing_distance"] = self._bucket_analyze(
            trades, "trailing_distance_pct", [0.35, 0.5, 0.6, 0.75, 1.0]
        )
        results["by_partial_close"] = self._bucket_analyze(
            trades, "partial_close_pct", [30, 50]
        )
        results["by_score_min"] = self._bucket_analyze(
            trades, "min_score_total", [55, 60, 65, 70]
        )
        results["by_trailing_mode"] = self._group_analyze_cfg(trades, "trailing_mode")
        results["by_atr_sl"] = self._group_analyze_cfg(trades, "atr_sl_enabled")

        return results

    def _group_analyze(self, trades: list[dict], key: str) -> dict[str, Any]:
        """Group trades by a direct field value."""
        groups: dict[str, list] = {}
        for t in trades:
            val = str(t.get(key) or "unknown")
            groups.setdefault(val, []).append(t)
        return {k: self._stats(v) for k, v in groups.items() if len(v) >= 2}

    def _group_analyze_cfg(self, trades: list[dict], key: str) -> dict[str, Any]:
        """Group trades by a config_snapshot field value."""
        groups: dict[str, list] = {}
        for t in trades:
            val = (t.get("config_snapshot") or {}).get(key)
            if val is None:
                # Try the flattened cfg_ field
                val = t.get(f"cfg_{key}")
            if val is None:
                val = "unknown"
            groups.setdefault(str(val), []).append(t)
        return {k: self._stats(v) for k, v in groups.items() if len(v) >= 2}

    def _bucket_analyze(
        self, trades: list[dict], key: str, thresholds: list[float]
    ) -> dict[str, Any]:
        """Bucket trades by a config param value."""
        results: dict[str, list] = {}
        for t in trades:
            # Try config_snapshot first, then flattened cfg_ field
            val = (t.get("config_snapshot") or {}).get(key)
            if val is None:
                val = t.get(f"cfg_{key}")
            if val is not None:
                bucket = str(val)
                results.setdefault(bucket, []).append(t)
        return {k: self._stats(v) for k, v in results.items() if len(v) >= 2}

    @staticmethod
    def _stats(trades: list[dict]) -> dict[str, Any]:
        """Compute stats for a group of trades."""
        pnls = [t.get("pnl", 0) or 0 for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        losses_sum = abs(sum(p for p in pnls if p < 0))
        gains_sum = sum(p for p in pnls if p > 0)
        return {
            "trades": len(trades),
            "wins": wins,
            "winrate": round(wins / len(trades), 4) if trades else 0,
            "expectancy": round(float(np.mean(pnls)), 4) if pnls else 0,
            "total_pnl": round(sum(pnls), 4),
            "profit_factor": round(gains_sum / losses_sum, 4) if losses_sum != 0 else 0,
        }
