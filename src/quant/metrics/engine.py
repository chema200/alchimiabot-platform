"""Metrics Engine: institutional-grade performance measurement.

Computes all KPIs from enriched trades dataset.
Output: structured JSON with global, by_coin, by_score, by_side breakdowns.
"""

from typing import Any
import numpy as np


class MetricsEngine:
    """Computes comprehensive trading metrics from enriched trades."""

    def compute(self, trades: list[dict]) -> dict[str, Any]:
        """Compute all metrics from enriched trades."""
        if not trades:
            return {"global": {}, "by_coin": {}, "by_side": {}, "by_score": {}, "by_mode": {}, "by_hour": {}, "by_exit": {}}

        return {
            "global": self._compute_group(trades),
            "by_coin": self._compute_breakdown(trades, "coin"),
            "by_side": self._compute_breakdown(trades, "side"),
            "by_score": self._compute_breakdown(trades, "score_bucket"),
            "by_mode": self._compute_breakdown(trades, "mode"),
            "by_hour": self._compute_by_hour(trades),
            "by_exit": self._compute_breakdown(trades, "exit_type"),
            "by_micro": self._compute_by_micro_bucket(trades),
            "risk": self._compute_risk(trades),
            "execution": self._compute_execution(trades),
        }

    def _compute_group(self, trades: list[dict]) -> dict[str, Any]:
        """Core metrics for a group of trades."""
        if not trades:
            return {}

        pnls = [t.get("pnl", 0) or 0 for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total = len(pnls)
        win_count = len(wins)
        loss_count = len(losses)

        winrate = win_count / total if total > 0 else 0
        avg_win = float(np.mean(wins)) if wins else 0
        avg_loss = float(np.mean(losses)) if losses else 0
        expectancy = avg_win * winrate + avg_loss * (1 - winrate)

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

        total_pnl = sum(pnls)
        total_fees = sum(t.get("fee", 0) or 0 for t in trades)

        return {
            "trades": total,
            "wins": win_count,
            "losses": loss_count,
            "winrate": round(winrate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "expectancy": round(expectancy, 4),
            "profit_factor": round(profit_factor, 4),
            "total_pnl": round(total_pnl, 4),
            "total_fees": round(total_fees, 4),
            "net_pnl": round(total_pnl, 4),
            "avg_pnl": round(total_pnl / total, 4) if total > 0 else 0,
            "fee_killed": sum(1 for t in trades if t.get("fee_killed")),
        }

    def _compute_breakdown(self, trades: list[dict], key: str) -> dict[str, Any]:
        """Compute metrics grouped by a key."""
        groups: dict[str, list] = {}
        for t in trades:
            val = str(t.get(key, "unknown") or "unknown")
            groups.setdefault(val, []).append(t)

        return {k: self._compute_group(v) for k, v in sorted(groups.items())}

    def _compute_by_hour(self, trades: list[dict]) -> dict[str, Any]:
        """Metrics by hour of day."""
        groups: dict[int, list] = {}
        for t in trades:
            ts = t.get("timestamp")
            if ts:
                hour = ts.hour if hasattr(ts, "hour") else 0
                groups.setdefault(hour, []).append(t)

        return {str(h): self._compute_group(v) for h, v in sorted(groups.items())}

    def _compute_by_micro_bucket(self, trades: list[dict]) -> dict[str, Any]:
        """Metrics by microstructure score bucket."""
        groups: dict[str, list] = {}
        for t in trades:
            ms = t.get("micro_score") or 0
            if ms >= 70:
                bucket = "70+"
            elif ms >= 50:
                bucket = "50-70"
            elif ms >= 35:
                bucket = "35-50"
            else:
                bucket = "0-35"
            groups.setdefault(bucket, []).append(t)

        return {k: self._compute_group(v) for k, v in sorted(groups.items())}

    def _compute_risk(self, trades: list[dict]) -> dict[str, Any]:
        """Risk metrics: drawdown, tail risk, consecutive losses."""
        pnls = [t.get("pnl", 0) or 0 for t in trades]
        if not pnls:
            return {}

        # Max drawdown
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdowns = peak - cumulative
        max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0

        # Consecutive losses
        max_consec_loss = 0
        current_streak = 0
        for p in pnls:
            if p <= 0:
                current_streak += 1
                max_consec_loss = max(max_consec_loss, current_streak)
            else:
                current_streak = 0

        # Tail risk
        losses = sorted([p for p in pnls if p < 0])
        p95_loss = float(np.percentile(losses, 5)) if len(losses) >= 5 else (losses[0] if losses else 0)
        p99_loss = float(np.percentile(losses, 1)) if len(losses) >= 10 else (losses[0] if losses else 0)

        # Sharpe
        if len(pnls) > 1:
            std = float(np.std(pnls))
            sharpe = float(np.mean(pnls)) / std * np.sqrt(252 * 24) if std > 0 else 0
        else:
            sharpe = 0

        return {
            "max_drawdown": round(max_dd, 4),
            "max_consecutive_losses": max_consec_loss,
            "p95_loss": round(p95_loss, 4),
            "p99_loss": round(p99_loss, 4),
            "sharpe": round(sharpe, 4),
            "total_trades": len(pnls),
        }

    def _compute_execution(self, trades: list[dict]) -> dict[str, Any]:
        """Execution quality metrics."""
        durations = [t.get("duration_seconds", 0) or 0 for t in trades]
        mfes = [t.get("mfe_pct", 0) or 0 for t in trades]
        maes = [t.get("mae_pct", 0) or 0 for t in trades]

        # Exit efficiency: how much of MFE was captured
        efficiencies = []
        for t in trades:
            mfe = t.get("mfe_pct", 0) or 0
            pnl_pct = t.get("pnl_pct", 0) or 0
            if mfe > 0:
                efficiencies.append(pnl_pct / mfe)

        return {
            "avg_duration_sec": round(float(np.mean(durations)), 0) if durations else 0,
            "avg_mfe_pct": round(float(np.mean(mfes)), 4) if mfes else 0,
            "avg_mae_pct": round(float(np.mean(maes)), 4) if maes else 0,
            "avg_exit_efficiency": round(float(np.mean(efficiencies)), 4) if efficiencies else 0,
        }
