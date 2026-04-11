"""Validation Runner: orchestrates experiment batches and generates verdicts.

Runs 3 isolated batches against real trade data, compares each against baseline,
and produces actionable verdicts with risk flags.
"""

from typing import Any

import structlog

from ..metrics.engine import MetricsEngine
from ..experiments.engine import ExperimentEngine, ExperimentConfig
from .configs import get_batches
from .report import ValidationReport, BatchResult, BatchVerdict

logger = structlog.get_logger()

# Verdict thresholds
MIN_TRADES = 10
EPSILON_EQUALITY = 0.02        # 2% - if metrics differ by less, consider them equal
MIN_IMPROVEMENT_FOR_TEST = 5.0  # 5% improvement for TEST_LIVE
MIN_IMPROVEMENT_FOR_ADOPT = 15.0  # 15% improvement for ADOPT
MAX_DD_INCREASE_FOR_ADOPT = 20.0  # max 20% DD increase for ADOPT


class ValidationRunner:
    """Runs full validation: 3 experiment batches with verdicts."""

    def __init__(self) -> None:
        self._metrics = MetricsEngine()
        self._experiments = ExperimentEngine()

    def run_full_validation(self, trades: list[dict], mode: str = "live_trades",
                            date_from: str | None = None, date_to: str | None = None,
                            coins: list[str] | None = None) -> ValidationReport:
        """Run all 3 batches and generate complete report.

        mode: "live_trades" (uses provided trades) or "replay_historical" (builds from parquet)
        """
        if mode == "replay_historical":
            from ..datasets.replay_builder import ReplayBuilder
            builder = ReplayBuilder()
            summary = builder.get_data_summary()
            if summary["status"] == "NO_DATA":
                report = ValidationReport()
                report.baseline_summary = {"status": "NO_DATA", "note": "No historical parquet data found"}
                return report
            trades = builder.build(date_from=date_from, date_to=date_to, coins=coins)
            if not trades:
                report = ValidationReport()
                report.baseline_summary = {"status": "NO_DATA", "note": "Replay generated 0 trades from historical data"}
                return report

        report = ValidationReport()
        report.total_trades_baseline = len(trades)

        # Baseline
        baseline = self._metrics.compute(trades)
        bg = baseline.get("global", {})
        report.baseline_summary = {
            "expectancy": bg.get("expectancy", 0),
            "profit_factor": bg.get("profit_factor", 0),
            "winrate": bg.get("winrate", 0),
            "net_pnl": bg.get("net_pnl", 0),
            "max_drawdown": baseline.get("risk", {}).get("max_drawdown", 0),
            "avg_exit_efficiency": baseline.get("execution", {}).get("avg_exit_efficiency", 0),
            "fee_killed_count": bg.get("fee_killed", 0),
            "trades": bg.get("trades", 0),
            "avg_win": bg.get("avg_win", 0),
            "avg_loss": bg.get("avg_loss", 0),
        }

        # Run each batch
        batches = get_batches()
        adopt_count = 0
        best_configs = {}
        integrity_issues = []

        for batch_name, batch_def in batches.items():
            batch_result = self._run_batch(trades, batch_name, batch_def, report.baseline_summary)
            report.batches.append(batch_result)

            # Track integrity issues from blockers
            if batch_result.data_quality_blockers:
                integrity_issues.extend(
                    f"[{batch_name}] {b}" for b in batch_result.data_quality_blockers
                )

            if batch_result.verdict.decision == "ADOPT":
                adopt_count += 1
                for exp in batch_result.experiments:
                    if exp["name"] == batch_result.best_experiment:
                        best_configs[batch_name] = exp.get("config_params", {})

        # Combination suggestion
        if adopt_count >= 2:
            combined = {}
            for batch_name, params in best_configs.items():
                combined.update(params)
            report.combination_suggestion = {
                "proposed_config": combined,
                "note": f"Combines best from {adopt_count} passing batches. Run as BATCH 4 before deploying.",
            }

        # Validation integrity
        if integrity_issues:
            report.validation_integrity = {"status": "DEGRADED", "issues": integrity_issues}
        else:
            report.validation_integrity = {"status": "OK", "issues": []}

        logger.info("validation.completed", trades=len(trades), batches=len(report.batches),
                     adopts=adopt_count)
        return report

    def run_single_batch(self, trades: list[dict], batch_name: str) -> BatchResult:
        """Run a single batch."""
        batches = get_batches()
        if batch_name not in batches:
            return BatchResult(batch_name, "Unknown batch", [], "", BatchVerdict("REJECT", "Batch not found"))

        baseline = self._metrics.compute(trades)
        bg = baseline.get("global", {})
        baseline_summary = {
            "expectancy": bg.get("expectancy", 0),
            "profit_factor": bg.get("profit_factor", 0),
            "winrate": bg.get("winrate", 0),
            "net_pnl": bg.get("net_pnl", 0),
            "max_drawdown": baseline.get("risk", {}).get("max_drawdown", 0),
            "trades": bg.get("trades", 0),
        }

        return self._run_batch(trades, batch_name, batches[batch_name], baseline_summary)

    def _run_batch(self, trades: list[dict], batch_name: str, batch_def: dict,
                   baseline: dict) -> BatchResult:
        """Run one batch of experiments."""
        hypothesis = batch_def["hypothesis"]
        configs = batch_def["configs"]

        results = self._experiments.run_multiple(trades, configs)

        experiments = []
        best_name = ""
        best_expectancy = -999

        for config, result in zip(configs, results):
            eg = result.experiment_metrics.get("global", {})
            er = result.experiment_metrics.get("risk", {})
            ee = result.experiment_metrics.get("execution", {})

            exp_expectancy = eg.get("expectancy", 0)
            exp_dd = er.get("max_drawdown", 0)
            baseline_dd = baseline.get("max_drawdown", 0)
            baseline_exp = baseline.get("expectancy", 0)

            # Check constraints
            valid = result.trades_accepted >= 5
            dd_ok = baseline_dd == 0 or exp_dd <= baseline_dd * 1.5

            # Vs baseline changes
            exp_change = ((exp_expectancy - baseline_exp) / abs(baseline_exp) * 100) if baseline_exp != 0 else 0
            pf_change = 0
            if baseline.get("profit_factor", 0) != 0:
                pf_change = ((eg.get("profit_factor", 0) - baseline["profit_factor"]) / baseline["profit_factor"] * 100)
            trades_change = ((result.trades_accepted - baseline.get("trades", 0)) / max(baseline.get("trades", 1), 1) * 100)
            dd_change = ((exp_dd - baseline_dd) / max(abs(baseline_dd), 0.01) * 100) if baseline_dd != 0 else 0

            exp_data = {
                "name": config.name,
                "trades_accepted": result.trades_accepted,
                "trades_rejected": result.trades_rejected,
                "config_params": self._extract_changed_params(config),
                "key_metrics": {
                    "expectancy": round(exp_expectancy, 4),
                    "profit_factor": round(eg.get("profit_factor", 0), 4),
                    "winrate": round(eg.get("winrate", 0), 4),
                    "net_pnl": round(eg.get("net_pnl", 0), 4),
                    "max_drawdown": round(exp_dd, 4),
                    "avg_win": round(eg.get("avg_win", 0), 4),
                    "avg_loss": round(eg.get("avg_loss", 0), 4),
                    "fee_killed": eg.get("fee_killed", 0),
                    "exit_efficiency": round(ee.get("avg_exit_efficiency", 0), 4),
                },
                "vs_baseline": {
                    "expectancy_change_pct": round(exp_change, 1),
                    "pf_change_pct": round(pf_change, 1),
                    "trades_change_pct": round(trades_change, 1),
                    "dd_change_pct": round(dd_change, 1),
                },
            }
            experiments.append(exp_data)

            # Track best (by expectancy, with constraints)
            if valid and dd_ok and exp_expectancy > best_expectancy:
                best_expectancy = exp_expectancy
                best_name = config.name

        # Detect batch-level status flags and data quality blockers
        status_flags = self._detect_status_flags(experiments, batch_name, trades)
        data_quality_blockers = self._detect_data_quality_blockers(
            experiments, batch_name, trades, baseline
        )

        # Generate verdict (pass flags for smarter decisions)
        verdict = self._generate_verdict(
            experiments, best_name, baseline, status_flags, data_quality_blockers
        )

        return BatchResult(
            batch_name=batch_name,
            hypothesis=hypothesis,
            experiments=experiments,
            best_experiment=best_name,
            verdict=verdict,
            batch_status_flags=status_flags,
            data_quality_blockers=data_quality_blockers,
        )

    def _detect_status_flags(self, experiments: list[dict], batch_name: str,
                              trades: list[dict]) -> list[str]:
        """Detect batch-level status anomalies."""
        flags = []

        if len(experiments) < 2:
            return flags

        # Check if all variants produce identical trade counts
        trade_counts = [e["trades_accepted"] for e in experiments]
        if len(set(trade_counts)) == 1:
            flags.append("NO_TRADE_CHANGE")

        # Check if all variants have effectively identical metrics (within EPSILON)
        all_identical = True
        ref = experiments[0]["key_metrics"]
        for exp in experiments[1:]:
            km = exp["key_metrics"]
            for key in ("expectancy", "profit_factor", "winrate", "net_pnl"):
                ref_val = abs(ref.get(key, 0))
                exp_val = abs(km.get(key, 0))
                diff = abs(ref.get(key, 0) - km.get(key, 0))
                denom = max(ref_val, exp_val, 0.0001)
                if diff / denom > EPSILON_EQUALITY:
                    all_identical = False
                    break
            if not all_identical:
                break

        if all_identical:
            flags.append("NO_EFFECT")

        # For score_threshold batch: check if score filtering actually changed anything
        if batch_name == "score_threshold":
            # If all experiments have same trades_accepted, scores didn't filter
            if "NO_TRADE_CHANGE" in flags:
                flags.append("SCORE_NOT_APPLIED")

        return flags

    def _detect_data_quality_blockers(self, experiments: list[dict], batch_name: str,
                                        trades: list[dict], baseline: dict) -> list[str]:
        """Detect data quality issues that block valid conclusions."""
        blockers = []

        # For score_threshold: check if trades have non-zero scores
        # Enriched trades use "score_total" (aliased from signal_score in DB)
        if batch_name == "score_threshold":
            scores_found = 0
            for t in trades:
                score = t.get("score_total") or t.get("signal_score") or t.get("score", 0) or 0
                if float(score) > 0:
                    scores_found += 1
            coverage_pct = scores_found / len(trades) * 100 if trades else 0
            if scores_found == 0:
                blockers.append("All trades have score=0; score threshold experiments cannot discriminate")
            elif coverage_pct < 50:
                blockers.append(f"Only {coverage_pct:.0f}% of trades have scores ({scores_found}/{len(trades)}); results are partially reliable")

        # Check minimum trades
        if baseline.get("trades", 0) < MIN_TRADES:
            blockers.append(f"Only {baseline.get('trades', 0)} baseline trades (need {MIN_TRADES}+)")

        return blockers

    def _generate_verdict(self, experiments: list[dict], best_name: str,
                          baseline: dict, status_flags: list[str],
                          data_quality_blockers: list[str]) -> BatchVerdict:
        """Generate verdict for a batch with proper checks."""

        # BLOCKED_BY_DATA_QUALITY takes priority
        if data_quality_blockers:
            return BatchVerdict(
                "BLOCKED_BY_DATA_QUALITY",
                f"Cannot evaluate: {'; '.join(data_quality_blockers)}",
                risk_flags=["DATA_QUALITY"],
                confidence="LOW",
            )

        # NO_EFFECT means all variants are identical
        if "NO_EFFECT" in status_flags:
            reasons = []
            if "NO_TRADE_CHANGE" in status_flags:
                reasons.append(f"all {len(experiments)} variants accepted the same trades")
            if "SCORE_NOT_APPLIED" in status_flags:
                reasons.append("score thresholds had no filtering effect")
            reason_text = "; ".join(reasons) if reasons else "all variants produced identical metrics"
            return BatchVerdict(
                "INCONCLUSIVE",
                f"No effect detected: {reason_text}. "
                f"The parameter changes had no measurable impact on results.",
                risk_flags=["NO_EFFECT"],
                confidence="LOW",
            )

        if not best_name:
            return BatchVerdict(
                "INCONCLUSIVE",
                "No valid experiment found (all below constraints: <5 trades or DD too high)",
                confidence="LOW",
            )

        # Find best experiment data
        best = None
        for exp in experiments:
            if exp["name"] == best_name:
                best = exp
                break

        if not best:
            return BatchVerdict("INCONCLUSIVE", "Best experiment not found", confidence="LOW")

        # Check if best is the baseline (first experiment, or name contains "baseline")
        is_baseline = "baseline" in best_name.lower() or best_name == experiments[0]["name"]
        if is_baseline:
            return BatchVerdict(
                "INCONCLUSIVE",
                f"Best experiment '{best_name}' is the baseline itself. "
                f"None of the alternatives improved on the current config. "
                f"Baseline E=${baseline.get('expectancy', 0):.4f}, "
                f"PF={baseline.get('profit_factor', 0):.2f}.",
                risk_flags=["BEST_IS_BASELINE"],
                confidence="MEDIUM",
            )

        vs = best["vs_baseline"]
        km = best["key_metrics"]
        risk_flags = []

        # Risk flags
        if best["trades_accepted"] < 15:
            risk_flags.append("LOW_SAMPLE")
        if vs["dd_change_pct"] > MAX_DD_INCREASE_FOR_ADOPT:
            risk_flags.append("DD_INCREASE")
        if vs["trades_change_pct"] < -60:
            risk_flags.append("TRADE_REDUCTION_HIGH")

        exp_change = vs["expectancy_change_pct"]
        pf_change = vs["pf_change_pct"]
        dd_change = vs["dd_change_pct"]

        # Absolute system health check: if system is clearly losing money,
        # cap verdict at TEST_LIVE maximum
        system_unhealthy = km["expectancy"] < 0 and km["profit_factor"] < 0.8
        if system_unhealthy:
            risk_flags.append("SYSTEM_NEGATIVE")

        # Build specific reasoning with actual numbers
        best_detail = (
            f"'{best_name}': E=${km['expectancy']:.4f} ({exp_change:+.1f}%), "
            f"PF={km['profit_factor']:.2f} ({pf_change:+.1f}%), "
            f"WR={km['winrate']*100:.0f}%, "
            f"trades={best['trades_accepted']}, "
            f"DD={km['max_drawdown']:.4f} ({dd_change:+.1f}%)"
        )

        # Decision logic with proper thresholds
        if best["trades_accepted"] < MIN_TRADES:
            decision = "INCONCLUSIVE"
            reasoning = (
                f"Only {best['trades_accepted']} trades accepted -- insufficient for confidence. "
                f"{best_detail}."
            )
            confidence = "LOW"
        elif system_unhealthy:
            # System losing money -- cap at TEST_LIVE even with improvement
            if exp_change > MIN_IMPROVEMENT_FOR_TEST:
                decision = "TEST_LIVE"
                reasoning = (
                    f"Improvement detected but system is still negative "
                    f"(E=${km['expectancy']:.4f}, PF={km['profit_factor']:.2f}). "
                    f"Capped at TEST_LIVE. {best_detail}."
                )
            else:
                decision = "REJECT"
                reasoning = (
                    f"System is negative and no meaningful improvement. {best_detail}."
                )
            confidence = "LOW"
        elif exp_change > MIN_IMPROVEMENT_FOR_ADOPT and pf_change > 0 and dd_change < MAX_DD_INCREASE_FOR_ADOPT:
            decision = "ADOPT"
            reasoning = f"Strong improvement with acceptable risk. {best_detail}."
            confidence = "HIGH" if best["trades_accepted"] >= 30 else "MEDIUM"
        elif exp_change > MIN_IMPROVEMENT_FOR_TEST and dd_change < 50:
            decision = "TEST_LIVE"
            reasoning = (
                f"Moderate improvement, worth testing in paper/shadow mode. {best_detail}."
            )
            confidence = "MEDIUM" if best["trades_accepted"] >= 20 else "LOW"
        else:
            decision = "REJECT"
            reasoning = f"No clear improvement or risk too high. {best_detail}."
            confidence = "MEDIUM" if best["trades_accepted"] >= 20 else "LOW"

        return BatchVerdict(decision, reasoning, risk_flags, confidence)

    def _extract_changed_params(self, config: ExperimentConfig) -> dict:
        """Extract only the params that differ from default."""
        default = ExperimentConfig(name="default")
        changed = {}
        for field in ["score_min", "micro_min", "sl_max_pct", "tp_min_pct",
                       "trailing_distance_pct", "partial_close_pct",
                       "use_micro", "use_rsi", "use_spread", "use_btc_filter"]:
            val = getattr(config, field)
            default_val = getattr(default, field)
            if val != default_val:
                changed[field] = val
        return changed
