"""Entry Quality Analyzer: understand WHY trades lose by analyzing entry diagnostics.

Compares winning vs losing trades across quality labels, late entry risk,
and diagnostic fields to identify the losing trade pattern.
"""

from typing import Any

import numpy as np


class EntryQualityAnalyzer:
    """Analyze entry quality diagnostics to find losing trade patterns."""

    def analyze(self, trades: list[dict], signals: list[dict] | None = None) -> dict:
        results: dict[str, Any] = {}

        # WR/expectancy by quality label
        results["by_quality_label"] = self._group_stats(trades, "entry_quality_label")

        # PnL by late_entry_risk
        results["by_late_risk"] = self._group_stats(trades, "late_entry_risk")

        # Losing vs winning trade profiles
        winners = [t for t in trades if (t.get("pnl") or 0) > 0]
        losers = [t for t in trades if (t.get("pnl") or 0) <= 0]
        results["winning_profile"] = self._build_profile(winners)
        results["losing_profile"] = self._build_profile(losers)

        # Top 3 factors in losers vs winners
        results["losing_factors"] = self._top_factors(losers)
        results["winning_factors"] = self._top_factors(winners)

        return results

    def _group_stats(self, trades: list[dict], key: str) -> dict:
        groups: dict[str, list] = {}
        for t in trades:
            val = t.get(key) or "unknown"
            groups.setdefault(val, []).append(t)
        return {k: {
            "trades": len(v),
            "winrate": round(sum(1 for t in v if (t.get("pnl") or 0) > 0) / max(len(v), 1), 4),
            "expectancy": round(float(np.mean([t.get("pnl", 0) or 0 for t in v])), 4),
            "avg_pnl": round(float(np.mean([t.get("pnl", 0) or 0 for t in v])), 4),
            "total_pnl": round(sum(t.get("pnl", 0) or 0 for t in v), 4),
        } for k, v in sorted(groups.items()) if len(v) >= 1}

    def _build_profile(self, trades: list[dict]) -> dict:
        if not trades:
            return {"count": 0}
        # Extract diagnostics from entry_diagnostics or direct fields
        scores = [t.get("score_total") or t.get("entry_diagnostics", {}).get("score_total", 0) or 0 for t in trades]
        spreads = [t.get("entry_diagnostics", {}).get("spread_pct", 0) or 0
                   for t in trades if t.get("entry_diagnostics")]
        move_fee = [t.get("entry_diagnostics", {}).get("expected_move_vs_fee_ratio", 0) or 0
                    for t in trades if t.get("entry_diagnostics")]
        confirms = [t.get("entry_diagnostics", {}).get("confirmation_strength", 0) or 0
                    for t in trades if t.get("entry_diagnostics")]

        return {
            "count": len(trades),
            "avg_score": round(float(np.mean(scores)), 2) if scores else 0,
            "avg_spread_pct": round(float(np.mean(spreads)), 4) if spreads else 0,
            "avg_move_vs_fee": round(float(np.mean(move_fee)), 2) if move_fee else 0,
            "avg_confirmation": round(float(np.mean(confirms)), 2) if confirms else 0,
            "quality_distribution": self._count_values(trades, "entry_quality_label"),
            "late_risk_distribution": self._count_values(trades, "late_entry_risk"),
        }

    def _count_values(self, trades: list[dict], key: str) -> dict:
        counts: dict[str, int] = {}
        for t in trades:
            val = t.get(key) or "unknown"
            counts[val] = counts.get(val, 0) + 1
        return counts

    def _top_factors(self, trades: list[dict]) -> list[dict]:
        if not trades:
            return []
        factors = []
        # Check common patterns
        weak = sum(1 for t in trades if t.get("entry_quality_label") == "WEAK")
        late_high = sum(1 for t in trades if t.get("late_entry_risk") == "HIGH")
        c_grade = sum(1 for t in trades if t.get("entry_quality_label") == "C")

        n = len(trades)
        if weak > 0:
            factors.append({"factor": "WEAK quality label", "count": weak, "pct": round(weak / n * 100, 1)})
        if late_high > 0:
            factors.append({"factor": "HIGH late entry risk", "count": late_high, "pct": round(late_high / n * 100, 1)})
        if c_grade > 0:
            factors.append({"factor": "C quality grade", "count": c_grade, "pct": round(c_grade / n * 100, 1)})

        return sorted(factors, key=lambda f: f["count"], reverse=True)[:3]
