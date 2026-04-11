"""Counterfactual Analysis: what-if scenarios for different score thresholds.

Given all signals (entered + blocked), simulate how many would have been
taken at various score_min thresholds.
"""


class CounterfactualAnalyzer:
    """Simulates alternative score thresholds against historical signals."""

    def analyze(
        self,
        signals: list[dict],
        score_thresholds: list[int] | None = None,
    ) -> dict:
        """For each threshold, count how many signals would have entered.

        Args:
            signals: list of signal dicts with signal_score and action fields.
            score_thresholds: list of score_min values to simulate.

        Returns:
            dict keyed by "score_{threshold}" with counts.
        """
        if score_thresholds is None:
            score_thresholds = [50, 55, 60, 65, 70, 75]

        if not signals:
            return {
                f"score_{t}": {
                    "threshold": t,
                    "would_enter": 0,
                    "would_block": 0,
                    "total_signals": 0,
                }
                for t in score_thresholds
            }

        # All signals that were actually ENTER (passed all filters)
        all_enters = [s for s in signals if s.get("action") == "ENTER"]

        results = {}
        for threshold in score_thresholds:
            would_enter = [
                s for s in all_enters
                if (s.get("signal_score") or 0) >= threshold
            ]
            would_block = [
                s for s in all_enters
                if (s.get("signal_score") or 0) < threshold
            ]
            results[f"score_{threshold}"] = {
                "threshold": threshold,
                "would_enter": len(would_enter),
                "would_block": len(would_block),
                "total_signals": len(signals),
                "enter_pct": round(len(would_enter) / len(signals) * 100, 1) if signals else 0,
            }

        return results
