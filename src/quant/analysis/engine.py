"""Analysis Engine: automatic pattern detection without manual intervention.

Runs all analyses and generates insights automatically.
"""

from typing import Any
import numpy as np

import structlog

logger = structlog.get_logger()


class AnalysisEngine:
    """Detects patterns and generates insights from enriched trades."""

    def analyze(self, trades: list[dict], signals: dict | None = None) -> dict[str, Any]:
        """Run all analyses and return structured insights."""
        if not trades:
            return {"insights": [], "warnings": [], "recommendations": []}

        results = {
            "score_effectiveness": self._score_effectiveness(trades),
            "micro_value": self._micro_value(trades),
            "filter_impact": self._filter_impact(trades, signals or {}),
            "exit_analysis": self._exit_analysis(trades),
            "holding_time": self._holding_time(trades),
            "coin_analysis": self._coin_analysis(trades),
            "side_analysis": self._side_analysis(trades),
            "fee_impact": self._fee_impact(trades),
            "rejection_analysis": self._rejection_analysis(trades, signals or {}),
        }

        # Generate insights from all analyses
        insights, warnings, recommendations = self._generate_insights(results, trades, signals or {})

        results["insights"] = insights
        results["warnings"] = warnings
        results["recommendations"] = recommendations

        return results

    def _score_effectiveness(self, trades: list[dict]) -> dict:
        """WR and PnL by score bucket."""
        buckets = {"0-40": [], "40-60": [], "60-80": [], "80+": []}
        for t in trades:
            score = t.get("score_total") or 0
            if score >= 80: buckets["80+"].append(t)
            elif score >= 60: buckets["60-80"].append(t)
            elif score >= 40: buckets["40-60"].append(t)
            else: buckets["0-40"].append(t)

        result = {}
        for bucket, group in buckets.items():
            if group:
                pnls = [t.get("pnl", 0) or 0 for t in group]
                wins = sum(1 for p in pnls if p > 0)
                result[bucket] = {
                    "trades": len(group),
                    "wr": round(wins / len(group) * 100, 1),
                    "avg_pnl": round(float(np.mean(pnls)), 4),
                    "total_pnl": round(sum(pnls), 4),
                }
        return result

    def _micro_value(self, trades: list[dict]) -> dict:
        """Compare trades with high vs low microstructure score."""
        high_micro = [t for t in trades if (t.get("micro_score") or 0) >= 50]
        low_micro = [t for t in trades if (t.get("micro_score") or 0) < 50]

        def stats(group):
            if not group:
                return {"trades": 0, "wr": 0, "avg_pnl": 0}
            pnls = [t.get("pnl", 0) or 0 for t in group]
            wins = sum(1 for p in pnls if p > 0)
            return {
                "trades": len(group),
                "wr": round(wins / len(group) * 100, 1),
                "avg_pnl": round(float(np.mean(pnls)), 4),
                "total_pnl": round(sum(pnls), 4),
            }

        return {"high_micro": stats(high_micro), "low_micro": stats(low_micro)}

    def _filter_impact(self, trades: list[dict], signals: dict) -> dict:
        """For each filter: how many blocked, WR of accepted vs theoretical WR of blocked."""
        result = {}

        # From signal evaluations
        for action, data in signals.items():
            result[action] = {
                "count": data.get("cnt", 0),
                "avg_score": data.get("avg_score", 0),
            }

        # Filter effectiveness from trades
        blocked_reasons = {}
        for action_data in signals.values():
            reasons = (action_data.get("reasons") or "").split(", ")
            for r in reasons:
                if r:
                    blocked_reasons[r] = blocked_reasons.get(r, 0) + 1

        result["blocked_reasons"] = blocked_reasons
        return result

    def _exit_analysis(self, trades: list[dict]) -> dict:
        """PnL by exit type."""
        exits: dict[str, list] = {}
        for t in trades:
            exit_type = t.get("exit_type", "unknown") or "unknown"
            exits.setdefault(exit_type, []).append(t)

        result = {}
        for exit_type, group in exits.items():
            pnls = [t.get("pnl", 0) or 0 for t in group]
            result[exit_type] = {
                "trades": len(group),
                "pct": round(len(group) / len(trades) * 100, 1),
                "avg_pnl": round(float(np.mean(pnls)), 4),
                "total_pnl": round(sum(pnls), 4),
                "wr": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1) if pnls else 0,
            }
        return result

    def _holding_time(self, trades: list[dict]) -> dict:
        """WR and PnL vs holding duration."""
        buckets = {"<1m": [], "1-3m": [], "3-10m": [], "10-30m": [], "30m+": []}
        for t in trades:
            dur = t.get("duration_seconds", 0) or 0
            if dur < 60: buckets["<1m"].append(t)
            elif dur < 180: buckets["1-3m"].append(t)
            elif dur < 600: buckets["3-10m"].append(t)
            elif dur < 1800: buckets["10-30m"].append(t)
            else: buckets["30m+"].append(t)

        result = {}
        for bucket, group in buckets.items():
            if group:
                pnls = [t.get("pnl", 0) or 0 for t in group]
                result[bucket] = {
                    "trades": len(group),
                    "wr": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
                    "avg_pnl": round(float(np.mean(pnls)), 4),
                }
        return result

    def _coin_analysis(self, trades: list[dict]) -> dict:
        """Best and worst coins."""
        coins: dict[str, list] = {}
        for t in trades:
            coin = t.get("coin", "?")
            coins.setdefault(coin, []).append(t)

        result = {}
        for coin, group in coins.items():
            if len(group) >= 2:
                pnls = [t.get("pnl", 0) or 0 for t in group]
                result[coin] = {
                    "trades": len(group),
                    "wr": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
                    "total_pnl": round(sum(pnls), 4),
                    "avg_pnl": round(float(np.mean(pnls)), 4),
                }

        return dict(sorted(result.items(), key=lambda x: x[1]["total_pnl"], reverse=True))

    def _side_analysis(self, trades: list[dict]) -> dict:
        """LONG vs SHORT performance."""
        sides: dict[str, list] = {}
        for t in trades:
            side = t.get("side", "?")
            sides.setdefault(side, []).append(t)

        result = {}
        for side, group in sides.items():
            pnls = [t.get("pnl", 0) or 0 for t in group]
            result[side] = {
                "trades": len(group),
                "wr": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
                "total_pnl": round(sum(pnls), 4),
                "avg_pnl": round(float(np.mean(pnls)), 4),
            }
        return result

    def _fee_impact(self, trades: list[dict]) -> dict:
        """How much fees eat into profits."""
        total_gross = sum(t.get("gross_pnl", 0) or 0 for t in trades)
        total_fees = sum(t.get("fee", 0) or 0 for t in trades)
        fee_killed = sum(1 for t in trades if t.get("fee_killed"))

        return {
            "total_gross": round(total_gross, 4),
            "total_fees": round(total_fees, 4),
            "fee_pct_of_gross": round(total_fees / abs(total_gross) * 100, 2) if total_gross != 0 else 0,
            "fee_killed_count": fee_killed,
            "fee_killed_pct": round(fee_killed / len(trades) * 100, 1) if trades else 0,
        }

    def _rejection_analysis(self, trades: list[dict], signals: dict) -> dict:
        """Count rejections by reason, compare scores of accepted vs blocked.
        Splits by decision_stage: PRE_CANDIDATE_REJECT vs BLOCKED_POST_CANDIDATE."""
        blocked = signals.get("BLOCKED", {})
        entered = signals.get("ENTER", {})

        blocked_count = blocked.get("cnt", 0)
        entered_count = entered.get("cnt", 0)
        total = blocked_count + entered_count

        # Parse blocked reasons into counts
        reasons_breakdown = {}
        raw_reasons = (blocked.get("reasons") or "").split(", ")
        for r in raw_reasons:
            if r:
                reasons_breakdown[r] = reasons_breakdown.get(r, 0) + 1

        # Decision stage breakdown (from signals dict if available)
        stage_breakdown = {}
        for stage_key in ("PRE_CANDIDATE_REJECT", "BLOCKED_POST_CANDIDATE", "ENTER"):
            stage_data = signals.get(stage_key, {})
            if stage_data:
                stage_breakdown[stage_key] = {
                    "count": stage_data.get("cnt", 0),
                    "avg_score": float(stage_data.get("avg_score") or 0),
                    "reasons": stage_data.get("reasons", ""),
                }

        # Score distribution of accepted trades
        entered_scores = [t.get("score_total") or 0 for t in trades]
        avg_entered_score = round(float(np.mean(entered_scores)), 2) if entered_scores else 0

        return {
            "total_signals": total,
            "entered": entered_count,
            "blocked": blocked_count,
            "rejection_rate": round(blocked_count / total * 100, 1) if total > 0 else 0,
            "avg_entered_score": avg_entered_score,
            "avg_blocked_score": float(blocked.get("avg_score") or 0),
            "reasons_breakdown": reasons_breakdown,
            "stage_breakdown": stage_breakdown,
        }

    def _generate_insights(self, results: dict, trades: list[dict],
                           signals: dict) -> tuple[list, list, list]:
        """Generate actionable insights from analysis results."""
        insights = []
        warnings = []
        recommendations = []

        # Score effectiveness
        score_data = results.get("score_effectiveness", {})
        high = score_data.get("80+", {})
        low = score_data.get("0-40", {})
        if high.get("wr", 0) > low.get("wr", 0) + 15:
            insights.append(f"High-score trades (80+) have {high['wr']}% WR vs {low.get('wr', 0)}% for low-score — score filter is working")
        if low.get("trades", 0) > 5 and low.get("wr", 0) < 35:
            recommendations.append(f"Consider raising score_min threshold — low-score trades have {low['wr']}% WR")

        # Micro value
        micro = results.get("micro_value", {})
        hm = micro.get("high_micro", {})
        lm = micro.get("low_micro", {})
        if hm.get("trades", 0) > 3 and lm.get("trades", 0) > 3:
            if hm.get("wr", 0) > lm.get("wr", 0) + 10:
                insights.append(f"Microstructure adds value: high micro WR={hm['wr']}% vs low={lm['wr']}%")
            elif lm.get("wr", 0) > hm.get("wr", 0):
                warnings.append(f"Microstructure may not help: low micro WR={lm['wr']}% >= high={hm['wr']}%")

        # Exit analysis
        exits = results.get("exit_analysis", {})
        sl_data = exits.get("SL", {})
        if sl_data.get("pct", 0) > 60:
            warnings.append(f"Too many SL exits: {sl_data['pct']}% — bot may be entering against trend")

        # Holding time
        hold = results.get("holding_time", {})
        fast = hold.get("<1m", {})
        if fast.get("trades", 0) > 5 and fast.get("wr", 0) < 30:
            warnings.append(f"Very fast trades (<1m) have {fast['wr']}% WR — likely noise entries")

        # Fee impact
        fees = results.get("fee_impact", {})
        if fees.get("fee_killed_pct", 0) > 10:
            warnings.append(f"{fees['fee_killed_pct']}% of trades killed by fees — SL may be too tight")
        if fees.get("fee_pct_of_gross", 0) > 25:
            recommendations.append(f"Fees consume {fees['fee_pct_of_gross']}% of gross — consider larger position sizes or fewer trades")

        # Side analysis
        sides = results.get("side_analysis", {})
        long_wr = sides.get("LONG", {}).get("wr", 50)
        short_wr = sides.get("SHORT", {}).get("wr", 50)
        if abs(long_wr - short_wr) > 20:
            better = "LONG" if long_wr > short_wr else "SHORT"
            worse = "SHORT" if better == "LONG" else "LONG"
            recommendations.append(f"{better} ({max(long_wr, short_wr)}% WR) significantly outperforms {worse} ({min(long_wr, short_wr)}% WR)")

        # Coin analysis
        coins = results.get("coin_analysis", {})
        poison = [(c, d) for c, d in coins.items() if d.get("avg_pnl", 0) < -0.3 and d.get("trades", 0) >= 3]
        if poison:
            coin_list = ", ".join(f"{c} ({d['avg_pnl']:.2f})" for c, d in poison[:5])
            recommendations.append(f"Consider blocking poison coins: {coin_list}")

        # Blocked signals
        blocked = signals.get("BLOCKED", {})
        entered = signals.get("ENTER", {})
        if blocked.get("cnt", 0) > 0 and entered.get("cnt", 0) > 0:
            block_ratio = blocked["cnt"] / (blocked["cnt"] + entered["cnt"]) * 100
            insights.append(f"Signal filter blocks {block_ratio:.0f}% of signals (BLOCKED={blocked['cnt']}, ENTER={entered['cnt']})")

        return insights, warnings, recommendations
