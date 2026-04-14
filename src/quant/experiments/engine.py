"""Experimentation Engine: simulate config changes WITHOUT touching bot live.

An experiment = alternative configuration applied to historical trades.
Recalculates which trades would have been taken/skipped and simulates outcomes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from ..metrics.engine import MetricsEngine

logger = structlog.get_logger()


@dataclass
class ExperimentConfig:
    """Alternative configuration to test."""
    name: str
    description: str = ""
    # Filter overrides
    score_min: float = 55
    micro_min: float = 35
    trend_min: float = 0
    # Risk overrides
    sl_max_pct: float = 0.40
    tp_min_pct: float = 0.45
    trailing_distance_pct: float = 0.50
    partial_close_pct: float = 30
    # Which filters to use
    use_micro: bool = True
    use_rsi: bool = True
    use_spread: bool = True
    use_btc_filter: bool = True
    use_overextension: bool = True
    # Confirm/Signal ratio filter (parabolic detection)
    use_ratio_filter: bool = False
    ratio_max: float = 0.60

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class ExperimentResult:
    """Results of running an experiment."""
    config: dict
    baseline_metrics: dict
    experiment_metrics: dict
    comparison: dict
    trades_accepted: int = 0
    trades_rejected: int = 0
    trades_baseline: int = 0

    def to_dict(self) -> dict:
        return {
            "config": self.config,
            "baseline": self.baseline_metrics,
            "experiment": self.experiment_metrics,
            "comparison": self.comparison,
            "trades_accepted": self.trades_accepted,
            "trades_rejected": self.trades_rejected,
            "trades_baseline": self.trades_baseline,
        }


class ExperimentEngine:
    """Runs experiments against historical enriched trades."""

    def __init__(self) -> None:
        self._metrics = MetricsEngine()

    def run(self, trades: list[dict], config: ExperimentConfig) -> ExperimentResult:
        """Run an experiment: filter trades by new config, recompute metrics, compare."""

        # Baseline = all actual trades as they happened
        baseline_metrics = self._metrics.compute(trades)

        # Experiment = apply new filters to decide which trades would have been taken
        accepted = []
        rejected = []

        for trade in trades:
            if self._would_accept(trade, config):
                # Simulate modified exit based on config
                simulated = self._simulate_exit(trade, config)
                accepted.append(simulated)
            else:
                rejected.append(trade)

        experiment_metrics = self._metrics.compute(accepted)

        # Compare key metrics
        comparison = self._compare(baseline_metrics.get("global", {}), experiment_metrics.get("global", {}))

        result = ExperimentResult(
            config=config.to_dict(),
            baseline_metrics=baseline_metrics,
            experiment_metrics=experiment_metrics,
            comparison=comparison,
            trades_accepted=len(accepted),
            trades_rejected=len(rejected),
            trades_baseline=len(trades),
        )

        logger.info("experiment.completed", name=config.name,
                     baseline_trades=len(trades), accepted=len(accepted),
                     baseline_pnl=baseline_metrics.get("global", {}).get("net_pnl", 0),
                     experiment_pnl=experiment_metrics.get("global", {}).get("net_pnl", 0))

        return result

    def run_multiple(self, trades: list[dict], configs: list[ExperimentConfig]) -> list[ExperimentResult]:
        """Run multiple experiments for comparison."""
        return [self.run(trades, cfg) for cfg in configs]

    def _would_accept(self, trade: dict, config: ExperimentConfig) -> bool:
        """Would this trade have been taken under the new config?"""
        # Try multiple field names for score (signal_score from DB, score_total from enriched)
        score = trade.get("score_total") or trade.get("signal_score") or 0
        micro = trade.get("micro_score") or 0
        trend = trade.get("trend_score") or 0

        # Score filter: always apply when score > 0
        if score > 0 and score < config.score_min:
            return False

        # Micro filter
        if config.use_micro and micro > 0 and micro < config.micro_min:
            return False

        # Trend filter
        if trend > 0 and trend < config.trend_min:
            return False

        # Confirm/Signal ratio filter (parabolic entry detection)
        # Block trades where the confirmation window did most of the move,
        # which suggests a late, exhausted entry.
        if config.use_ratio_filter:
            r = trade.get("confirm_signal_ratio")
            if r is not None and r > config.ratio_max:
                return False

        return True

    def _simulate_exit(self, trade: dict, config: ExperimentConfig) -> dict:
        """Simulate how the trade would have performed with modified risk params.

        This is an approximation based on MFE/MAE — not a full replay.
        """
        t = dict(trade)  # copy
        mfe = abs(t.get("mfe_pct", 0) or 0)
        mae = abs(t.get("mae_pct", 0) or 0)
        entry = t.get("entry_price", 0) or 0
        notional = t.get("notional", 0) or 100

        if entry <= 0:
            return t

        # Would the new SL have been hit?
        if mae >= config.sl_max_pct:
            # SL would have triggered — but maybe at a tighter level
            simulated_loss_pct = config.sl_max_pct
            t["pnl"] = -(simulated_loss_pct / 100 * notional * (t.get("leverage", 3) or 3))
            t["exit_type"] = "SL_SIM"
        elif mfe >= config.tp_min_pct:
            # TP would have been reached
            # With partial close: partial% at TP, rest trails
            partial_pct = config.partial_close_pct / 100
            remain_pct = 1 - partial_pct
            partial_gain = config.tp_min_pct / 100 * notional * (t.get("leverage", 3) or 3) * partial_pct
            # Remaining rides trailing — estimate capture at ~60% of MFE
            trail_gain = mfe * 0.6 / 100 * notional * (t.get("leverage", 3) or 3) * remain_pct
            t["pnl"] = partial_gain + trail_gain - (t.get("fee", 0) or 0)
            t["exit_type"] = "TP_SIM"
        # else: keep original result (trade played out similarly)

        t["outcome"] = "WIN" if (t.get("pnl", 0) or 0) > 0 else "LOSS"
        return t

    def _compare(self, baseline: dict, experiment: dict) -> dict:
        """Compare two metric sets."""
        keys = ["winrate", "avg_win", "avg_loss", "expectancy", "profit_factor",
                "net_pnl", "total_fees", "trades"]
        comparison = {}
        for k in keys:
            b = baseline.get(k, 0) or 0
            e = experiment.get(k, 0) or 0
            diff = e - b
            pct = (diff / abs(b) * 100) if b != 0 else 0
            comparison[k] = {
                "baseline": round(b, 4),
                "experiment": round(e, 4),
                "diff": round(diff, 4),
                "pct_change": round(pct, 2),
                "improved": diff > 0 if k != "avg_loss" else diff < 0,
            }
        return comparison
