"""Executive Summary Aggregator: single consolidated view of everything.

Combines live evidence, replay evidence, score parity, decisions,
and generates joint conclusions with confidence levels.
"""

from typing import Any

import structlog

from .analysis.score_parity import ScoreParityAnalyzer
from .datasets.trades_enriched import TradesEnrichedBuilder
from .metrics.engine import MetricsEngine
from .analysis.engine import AnalysisEngine
from .decision.engine import DecisionEngine

logger = structlog.get_logger()


class ExecutiveSummaryBuilder:
    """Builds the complete executive summary."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory
        self._enriched_builder = TradesEnrichedBuilder(session_factory)
        self._metrics_engine = MetricsEngine()
        self._analysis_engine = AnalysisEngine()
        self._decision_engine = DecisionEngine()
        self._score_parity = ScoreParityAnalyzer(session_factory)

    async def build(self, user_id: int = 1) -> dict[str, Any]:
        """Build the complete executive summary."""
        # Gather all data
        data = await self._enriched_builder.build_with_signals(user_id=user_id)
        trades = data["trades"]
        signals = data["signals"]

        metrics = self._metrics_engine.compute(trades) if trades else {}
        analysis = self._analysis_engine.analyze(trades, signals) if trades else {}
        decisions = self._decision_engine.generate(metrics, analysis, trades) if trades else []
        score_parity = await self._score_parity.analyze()

        # Extract key metrics
        g = metrics.get("global", {})
        risk = metrics.get("risk", {})

        total_trades = g.get("trades", 0)
        winrate = g.get("winrate", 0)
        expectancy = g.get("expectancy", 0)
        profit_factor = g.get("profit_factor", 0)
        net_pnl = g.get("net_pnl", 0)
        max_drawdown = risk.get("max_drawdown", 0)
        sharpe = risk.get("sharpe", 0)
        score_coverage = score_parity.get("coverage_pct", 0)

        # Live evidence
        live_evidence = {
            "status": "HAS_DATA" if total_trades > 0 else "NO_DATA",
            "trades": total_trades,
            "winrate": round(winrate * 100, 1) if winrate else 0,
            "expectancy": expectancy,
            "profit_factor": profit_factor,
            "net_pnl": net_pnl,
            "max_drawdown": max_drawdown,
            "sharpe": sharpe,
            "avg_win": g.get("avg_win", 0),
            "avg_loss": g.get("avg_loss", 0),
            "wins": g.get("wins", 0),
            "losses": g.get("losses", 0),
            "fee_killed": g.get("fee_killed", 0),
            "total_fees": g.get("total_fees", 0),
        }

        # Replay evidence — use real historical data if available
        replay_evidence = self._build_replay_evidence()

        # Edge status
        edge_status = self._compute_edge_status(
            total_trades, expectancy, profit_factor, score_coverage
        )

        # Confidence
        confidence = self._compute_confidence(
            total_trades, score_coverage, replay_evidence["status"]
        )

        # Data quality
        data_quality = self._compute_data_quality(score_parity, total_trades)

        # Joint conclusions
        joint_conclusions = self._compute_joint_conclusions(
            live_evidence, replay_evidence, score_parity
        )

        # What works, what fails, what to change, don't touch
        what_works = self._extract_what_works(analysis, metrics, decisions)
        what_fails = self._extract_what_fails(analysis, metrics, decisions)
        what_to_change = self._extract_what_to_change(decisions)
        do_not_touch = self._extract_do_not_touch(analysis, metrics)

        # Data quality issues
        data_quality_issues = self._extract_data_quality_issues(score_parity, analysis)

        # Next best action
        next_best_action = self._compute_next_best_action(
            decisions, data_quality_issues, total_trades, score_coverage
        )

        # Top recommendations (sorted by priority)
        top_recommendations = [d.to_dict() for d in decisions[:10]]

        summary = {
            "system_status": {
                "edge_status": edge_status,
                "confidence": confidence,
                "data_quality": data_quality,
            },
            "live_evidence": live_evidence,
            "replay_evidence": replay_evidence,
            "score_parity": score_parity,
            "joint_conclusions": joint_conclusions,
            "top_recommendations": top_recommendations,
            "next_best_action": next_best_action,
            "what_works": what_works,
            "what_fails": what_fails,
            "what_to_change": what_to_change,
            "do_not_touch": do_not_touch,
            "data_quality_issues": data_quality_issues,
            "total_trades": total_trades,
            "signals_summary": signals,
        }

        logger.info("executive_summary.built", edge=edge_status, confidence=confidence,
                     trades=total_trades, score_coverage=score_coverage)

        return summary

    def _compute_edge_status(self, trades: int, expectancy: float,
                              profit_factor: float, score_coverage: float) -> str:
        if trades < 30 or score_coverage < 50:
            return "INCONCLUSIVE"
        if expectancy < 0 or profit_factor < 0.5:
            return "NEGATIVE"
        if expectancy > 0 and profit_factor > 1.2:
            return "POSITIVE"
        if expectancy > 0 and profit_factor > 0.8:
            return "NEUTRAL"
        return "INCONCLUSIVE"

    def _compute_confidence(self, trades: int, score_coverage: float,
                             replay_status: str) -> str:
        if trades < 30 or score_coverage < 50:
            return "LOW"
        replay_confirms = replay_status in ("CONFIRMED", "HAS_DATA")
        if trades > 100 and score_coverage > 80 and replay_confirms:
            return "HIGH"
        if trades >= 30 and score_coverage > 50:
            return "MEDIUM"
        return "LOW"

    def _compute_data_quality(self, score_parity: dict, trades: int) -> str:
        # Use real_status (post-diagnostics era) when available so that legacy
        # data with missing scores does not drag down the overall assessment.
        # Fall back to global status if real_status is absent.
        status = score_parity.get("real_status") or score_parity.get("status", "BROKEN")
        if status == "OK" and trades >= 30:
            return "GOOD"
        elif status == "DEGRADED":
            return "DEGRADED"
        elif status == "NO_DATA" or trades == 0:
            return "NO_DATA"
        else:
            return "POOR"

    def _compute_joint_conclusions(self, live: dict, replay: dict,
                                    score_parity: dict) -> dict[str, Any]:
        live_has_data = live["status"] == "HAS_DATA" and live["trades"] > 0
        replay_has_data = replay["status"] not in ("NO_DATA", "NOT_AVAILABLE")
        score_ok = score_parity.get("status") in ("OK", "DEGRADED")

        if not live_has_data:
            classification = "INVALID"
            reasoning = "No live trade data available to draw conclusions"
        elif not score_ok:
            classification = "BLOCKED_BY_DATA_QUALITY"
            reasoning = f"Score parity is {score_parity.get('status')}: {score_parity.get('details')}"
        elif live["trades"] < 30:
            classification = "WEAK_EVIDENCE"
            reasoning = f"Only {live['trades']} trades - need at least 30 for meaningful conclusions"
        elif live_has_data and replay_has_data:
            # Both have data - compare
            if live["expectancy"] > 0 and live["profit_factor"] > 1.0:
                classification = "CONFIRMED"
                reasoning = "Both live and replay show positive edge"
            else:
                classification = "CONFLICTING"
                reasoning = "Live and replay evidence diverge"
        elif live_has_data and live["expectancy"] > 0 and live["profit_factor"] > 1.0:
            classification = "LIKELY"
            reasoning = f"Live shows positive edge (E={live['expectancy']}, PF={live['profit_factor']}), pending replay confirmation"
        elif live_has_data and live["expectancy"] <= 0:
            classification = "LIKELY"
            reasoning = f"Live shows negative edge (E={live['expectancy']}, PF={live['profit_factor']}), needs investigation"
        else:
            classification = "WEAK_EVIDENCE"
            reasoning = "Insufficient data for strong conclusions"

        return {
            "classification": classification,
            "reasoning": reasoning,
            "live_available": live_has_data,
            "replay_available": replay_has_data,
            "score_quality": score_parity.get("status", "UNKNOWN"),
        }

    def _extract_what_works(self, analysis: dict, metrics: dict,
                             decisions: list) -> list[str]:
        works = []
        g = metrics.get("global", {})

        # Check score effectiveness
        score_eff = analysis.get("score_effectiveness", {})
        high = score_eff.get("80+", {})
        if high.get("wr", 0) > 55 and high.get("trades", 0) >= 3:
            works.append(f"High-score signals (80+) have {high['wr']}% WR - score filter is discriminating well")

        # Check winrate
        if g.get("winrate", 0) > 0.5:
            works.append(f"Overall win rate {g['winrate']*100:.0f}% is above 50%")

        # Profit factor
        if g.get("profit_factor", 0) > 1.2:
            works.append(f"Profit factor {g['profit_factor']:.2f} shows positive edge")

        # Micro value
        micro = analysis.get("micro_value", {})
        hm = micro.get("high_micro", {})
        lm = micro.get("low_micro", {})
        if hm.get("trades", 0) >= 3 and hm.get("wr", 0) > lm.get("wr", 0) + 10:
            works.append(f"Microstructure filter adds value: high micro WR={hm['wr']}% vs low={lm.get('wr', 0)}%")

        # Check which side works
        sides = analysis.get("side_analysis", {})
        for side, data in sides.items():
            if data.get("wr", 0) > 55 and data.get("trades", 0) >= 5:
                works.append(f"{side} trades performing well: {data['wr']}% WR, avg ${data.get('avg_pnl', 0):.4f}")

        # Best coins
        coins = analysis.get("coin_analysis", {})
        good_coins = [(c, d) for c, d in coins.items()
                       if d.get("avg_pnl", 0) > 0.1 and d.get("trades", 0) >= 3]
        if good_coins:
            coin_list = ", ".join(f"{c} ({d['wr']}% WR)" for c, d in good_coins[:3])
            works.append(f"Strong coins: {coin_list}")

        # Insights from analysis
        for insight in analysis.get("insights", []):
            if insight not in works:
                works.append(insight)

        return works

    def _extract_what_fails(self, analysis: dict, metrics: dict,
                             decisions: list) -> list[str]:
        fails = []
        g = metrics.get("global", {})

        if g.get("winrate", 0) < 0.4 and g.get("trades", 0) > 10:
            fails.append(f"Win rate {g['winrate']*100:.0f}% is below 40%")

        if g.get("profit_factor", 0) < 1.0 and g.get("trades", 0) > 10:
            fails.append(f"Profit factor {g['profit_factor']:.2f} is below breakeven")

        if g.get("fee_killed", 0) > 0:
            total = g.get("trades", 1)
            pct = g["fee_killed"] / total * 100
            if pct > 5:
                fails.append(f"{g['fee_killed']} trades ({pct:.0f}%) killed by fees")

        # Warnings from analysis
        for warning in analysis.get("warnings", []):
            fails.append(warning)

        # Poison coins
        coins = analysis.get("coin_analysis", {})
        poison = [(c, d) for c, d in coins.items()
                   if d.get("avg_pnl", 0) < -0.2 and d.get("trades", 0) >= 3]
        if poison:
            coin_list = ", ".join(f"{c} (${d['avg_pnl']:.4f})" for c, d in poison[:3])
            fails.append(f"Poison coins losing money: {coin_list}")

        # Exit analysis issues
        exits = analysis.get("exit_analysis", {})
        sl = exits.get("SL", {})
        if sl.get("pct", 0) > 60:
            fails.append(f"SL exits too high: {sl['pct']}% of trades end in stop loss")

        return fails

    def _extract_what_to_change(self, decisions: list) -> list[dict]:
        changes = []
        for d in decisions:
            if d.type in ("recommendation", "warning"):
                changes.append({
                    "priority": d.confidence,
                    "title": d.title,
                    "action": d.action,
                    "evidence": d.evidence,
                    "expected_impact": d.expected_impact,
                    "type": d.type,
                })
        return changes[:8]

    def _extract_do_not_touch(self, analysis: dict, metrics: dict) -> list[str]:
        keep = []
        g = metrics.get("global", {})

        # If score filter works, keep it
        score_eff = analysis.get("score_effectiveness", {})
        high = score_eff.get("80+", {})
        low = score_eff.get("0-40", {})
        if high.get("wr", 0) > low.get("wr", 0) + 10:
            keep.append("Score threshold filter - clearly discriminates good vs bad trades")

        # If micro works, keep it
        micro = analysis.get("micro_value", {})
        hm = micro.get("high_micro", {})
        lm = micro.get("low_micro", {})
        if hm.get("trades", 0) >= 3 and hm.get("wr", 0) > lm.get("wr", 0) + 10:
            keep.append("Microstructure filter - adds measurable edge")

        # If fee impact is low, keep fee structure
        fees = analysis.get("fee_impact", {})
        if fees.get("fee_pct_of_gross", 0) < 15:
            keep.append("Current fee structure is efficient")

        # If a side is strong, keep it
        sides = analysis.get("side_analysis", {})
        for side, data in sides.items():
            if data.get("wr", 0) > 55 and data.get("trades", 0) >= 10:
                keep.append(f"{side} strategy - performing consistently well")

        if not keep:
            keep.append("Accumulate more data before drawing do-not-touch conclusions")

        return keep

    def _extract_data_quality_issues(self, score_parity: dict,
                                      analysis: dict) -> list[str]:
        # 2026-04-27: this list shows up as "Problemas de calidad" in the
        # Conclusions tab. We only flag REAL issues now — adopted/manual/
        # external trades are excluded upstream in score_parity.analyze()
        # so missing-score reports here mean the bot's scoring pipeline is
        # actually broken for bot-driven entries (vs the previous noise from
        # adopted positions which never had scores by design).
        issues = []
        for anomaly in score_parity.get("anomalies", []):
            issues.append(f"[{anomaly['severity'].upper()}] {anomaly['message']}")

        if score_parity.get("coverage_pct", 0) < 80:
            issues.append(
                f"Score coverage is only {score_parity['coverage_pct']}% on bot-driven entries — "
                f"adopted/manual/external trades are already excluded, so this means the live "
                f"scoring pipeline is dropping data on legit signals. Check engine logs."
            )

        if score_parity.get("status") == "NO_DATA":
            issues.append("No trade or signal data found in database")

        return issues

    def _compute_next_best_action(self, decisions: list, data_issues: list,
                                   trades: int, score_coverage: float) -> dict[str, Any]:
        # Priority 1: data quality
        if trades == 0:
            return {
                "action": "Start capturing trade data",
                "reasoning": "No trades in database. Ensure bot is sending data via PlatformBridge.",
                "priority": "CRITICAL",
                "category": "data",
            }

        if trades < 30:
            return {
                "action": f"Accumulate more trades (currently {trades}, need 30+)",
                "reasoning": "Insufficient sample size for statistically meaningful conclusions.",
                "priority": "HIGH",
                "category": "data",
            }

        if score_coverage < 50:
            return {
                "action": "Fix score pipeline - coverage too low",
                "reasoning": f"Only {score_coverage}% of trades have complete scores. Check bot signal scoring.",
                "priority": "HIGH",
                "category": "data_quality",
            }

        # Priority 2: high-confidence decisions
        high_confidence = [d for d in decisions if d.confidence == "high"]
        if high_confidence:
            d = high_confidence[0]
            return {
                "action": d.action,
                "reasoning": d.evidence,
                "priority": "MEDIUM",
                "category": "optimization",
                "title": d.title,
            }

        # Priority 3: medium-confidence decisions
        medium_confidence = [d for d in decisions if d.confidence == "medium"]
        if medium_confidence:
            d = medium_confidence[0]
            return {
                "action": d.action,
                "reasoning": d.evidence,
                "priority": "LOW",
                "category": "optimization",
                "title": d.title,
            }

        return {
            "action": "Continue monitoring - system is stable",
            "reasoning": "No high-priority actions needed. Keep accumulating data.",
            "priority": "LOW",
            "category": "monitoring",
        }

    def _build_replay_evidence(self) -> dict[str, Any]:
        """Build lightweight replay evidence from data summary only.

        Does NOT run the full ReplayBuilder.build() which scans all parquet
        files and can take 10+ seconds, blocking the async endpoint.
        The full replay simulation is available via the /api/quant/validation
        endpoint instead.
        """
        try:
            from .datasets.replay_builder import ReplayBuilder

            builder = ReplayBuilder()
            summary = builder.get_data_summary()

            if summary["status"] == "NO_DATA":
                return {"status": "NO_DATA", "note": "No historical parquet data captured yet"}

            return {
                "status": "DATA_AVAILABLE",
                "note": "Historical data present. Run full validation for detailed replay metrics.",
                "files": summary.get("files", 0),
                "days": summary.get("days", 0),
                "coins": summary.get("coins", 0),
                "date_range": summary.get("date_range", {}),
            }
        except Exception as e:
            logger.warning("executive_summary.replay_error", error=str(e))
            return {"status": "ERROR", "note": str(e)}
