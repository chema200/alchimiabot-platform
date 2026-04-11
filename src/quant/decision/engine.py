"""Decision Engine: generates automatic improvement suggestions.

Analyzes metrics and patterns to suggest specific parameter changes.
Every suggestion includes evidence, expected impact, and confidence.
"""

from typing import Any

import structlog

logger = structlog.get_logger()


class Decision:
    """A single improvement suggestion."""
    def __init__(self, type: str, title: str, evidence: str,
                 action: str, expected_impact: str, confidence: str = "medium") -> None:
        self.type = type  # insight, warning, recommendation
        self.title = title
        self.evidence = evidence
        self.action = action
        self.expected_impact = expected_impact
        self.confidence = confidence  # low, medium, high

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "title": self.title,
            "evidence": self.evidence,
            "action": self.action,
            "expected_impact": self.expected_impact,
            "confidence": self.confidence,
        }


class DecisionEngine:
    """Generates data-driven improvement suggestions."""

    def generate(self, metrics: dict, analysis: dict, trades: list[dict]) -> list[Decision]:
        """Generate all decisions from metrics and analysis."""
        decisions = []

        decisions.extend(self._check_score_threshold(analysis))
        decisions.extend(self._check_micro_filter(analysis))
        decisions.extend(self._check_trailing(metrics, trades))
        decisions.extend(self._check_sl_sizing(metrics, trades))
        decisions.extend(self._check_coin_blocking(analysis))
        decisions.extend(self._check_side_bias(analysis))
        decisions.extend(self._check_holding_time(analysis))
        decisions.extend(self._check_fee_optimization(analysis, metrics))

        # Sort by confidence
        priority = {"high": 0, "medium": 1, "low": 2}
        decisions.sort(key=lambda d: priority.get(d.confidence, 9))

        return decisions

    def _check_score_threshold(self, analysis: dict) -> list[Decision]:
        results = []
        scores = analysis.get("score_effectiveness", {})
        high = scores.get("80+", {})
        mid = scores.get("60-80", {})
        low = scores.get("40-60", {})
        vlow = scores.get("0-40", {})

        if vlow.get("trades", 0) >= 3 and vlow.get("wr", 0) < 35:
            results.append(Decision(
                "recommendation",
                "Raise score threshold",
                f"Low-score trades (0-40) have {vlow['wr']}% WR with {vlow['trades']} trades",
                "Increase score_min from current to 55-60",
                "Fewer trades but higher WR, better expectancy",
                "high" if vlow["trades"] >= 5 else "medium",
            ))

        if high.get("trades", 0) >= 3 and high.get("wr", 0) > 60:
            results.append(Decision(
                "insight",
                "High-score trades are strong",
                f"Score 80+ has {high['wr']}% WR, avg PnL ${high.get('avg_pnl', 0):.4f}",
                "Consider larger position size for high-score signals",
                "More profit from best setups",
                "medium",
            ))

        return results

    def _check_micro_filter(self, analysis: dict) -> list[Decision]:
        results = []
        micro = analysis.get("micro_value", {})
        hm = micro.get("high_micro", {})
        lm = micro.get("low_micro", {})

        if hm.get("trades", 0) >= 3 and lm.get("trades", 0) >= 3:
            if lm.get("wr", 0) > hm.get("wr", 0) + 5:
                results.append(Decision(
                    "warning",
                    "Microstructure filter may hurt",
                    f"Low micro WR={lm['wr']}% > high micro WR={hm['wr']}%",
                    "Test experiment: disable micro filter (use_micro=false)",
                    "Could increase WR if micro is false signal",
                    "medium",
                ))
            elif hm.get("wr", 0) > lm.get("wr", 0) + 15:
                results.append(Decision(
                    "insight",
                    "Microstructure adds strong value",
                    f"High micro WR={hm['wr']}% vs low={lm['wr']}% (diff={hm['wr']-lm['wr']:.0f}pp)",
                    "Keep micro filter, consider raising micro_min",
                    "Better signal quality",
                    "high",
                ))

        return results

    def _check_trailing(self, metrics: dict, trades: list[dict]) -> list[Decision]:
        results = []
        execution = metrics.get("execution", {})
        efficiency = execution.get("avg_exit_efficiency", 0)

        if efficiency < 0.3 and len(trades) >= 10:
            avg_mfe = execution.get("avg_mfe_pct", 0)
            results.append(Decision(
                "warning",
                "Low exit efficiency — trailing may be too tight",
                f"Average exit captures only {efficiency*100:.0f}% of MFE ({avg_mfe:.2f}%)",
                "Increase trailing_distance_pct or reduce partial_close_pct",
                "Capture more of the favorable move",
                "high",
            ))

        return results

    def _check_sl_sizing(self, metrics: dict, trades: list[dict]) -> list[Decision]:
        results = []
        global_m = metrics.get("global", {})
        avg_win = abs(global_m.get("avg_win", 0))
        avg_loss = abs(global_m.get("avg_loss", 0))

        if avg_loss > 0 and avg_win > 0 and avg_loss > avg_win * 2:
            results.append(Decision(
                "recommendation",
                "Loss asymmetry: avg loss >> avg win",
                f"Avg win=${avg_win:.4f}, avg loss=${avg_loss:.4f} (ratio {avg_win/avg_loss:.2f}x)",
                "Reduce sl_max_pct or increase tp_min_pct",
                "Better risk/reward ratio",
                "high",
            ))

        return results

    def _check_coin_blocking(self, analysis: dict) -> list[Decision]:
        results = []
        coins = analysis.get("coin_analysis", {})
        poison = [(c, d) for c, d in coins.items() if d.get("avg_pnl", 0) < -0.3 and d.get("trades", 0) >= 3]

        if poison:
            for coin, data in poison[:3]:
                results.append(Decision(
                    "recommendation",
                    f"Block {coin} — consistent loser",
                    f"{coin}: {data['trades']} trades, WR={data['wr']}%, avg PnL=${data['avg_pnl']:.4f}",
                    f"Add {coin} to blocked coins list",
                    f"Avoid ~${abs(data['total_pnl']):.2f} in losses",
                    "high" if data["trades"] >= 5 else "medium",
                ))

        return results

    def _check_side_bias(self, analysis: dict) -> list[Decision]:
        results = []
        sides = analysis.get("side_analysis", {})
        long_d = sides.get("LONG", {})
        short_d = sides.get("SHORT", {})

        if long_d.get("trades", 0) >= 5 and short_d.get("trades", 0) >= 5:
            diff = abs(long_d.get("wr", 50) - short_d.get("wr", 50))
            if diff > 20:
                worse = "SHORT" if long_d.get("wr", 0) > short_d.get("wr", 0) else "LONG"
                results.append(Decision(
                    "recommendation",
                    f"Consider reducing {worse} trades",
                    f"LONG WR={long_d.get('wr', 0)}%, SHORT WR={short_d.get('wr', 0)}% (diff={diff:.0f}pp)",
                    f"Raise entry threshold for {worse} side",
                    "Better WR by focusing on stronger side",
                    "medium",
                ))

        return results

    def _check_holding_time(self, analysis: dict) -> list[Decision]:
        results = []
        hold = analysis.get("holding_time", {})
        fast = hold.get("<1m", {})
        slow = hold.get("30m+", {})

        if fast.get("trades", 0) >= 3 and fast.get("wr", 0) < 30:
            results.append(Decision(
                "warning",
                "Ultra-fast trades performing poorly",
                f"Trades <1m: {fast['trades']} trades, WR={fast['wr']}%",
                "May indicate false breakout entries — increase confirmation window",
                "Fewer noise entries",
                "medium",
            ))

        if slow.get("trades", 0) >= 3 and slow.get("wr", 0) < 30:
            results.append(Decision(
                "warning",
                "Long-held trades performing poorly",
                f"Trades >30m: {slow['trades']} trades, WR={slow['wr']}%",
                "Consider reducing timeout or tightening trailing after 15m",
                "Cut losers faster",
                "medium",
            ))

        return results

    def _check_fee_optimization(self, analysis: dict, metrics: dict) -> list[Decision]:
        results = []
        fees = analysis.get("fee_impact", {})

        if fees.get("fee_killed_pct", 0) > 5:
            results.append(Decision(
                "recommendation",
                "Fees killing too many trades",
                f"{fees['fee_killed_pct']}% of trades gross positive but net negative",
                "Increase SL floor above fee dead zone, or reduce trade frequency",
                "Eliminate fee-killed trades",
                "high",
            ))

        if fees.get("fee_pct_of_gross", 0) > 30:
            results.append(Decision(
                "warning",
                "Fees consuming too much of gross profits",
                f"Fees = {fees['fee_pct_of_gross']}% of gross PnL",
                "Trade less frequently or with larger TP targets",
                "Better net/gross ratio",
                "medium",
            ))

        return results
