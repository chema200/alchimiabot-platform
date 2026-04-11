"""Score Parity Analyzer: checks how many trades/signals have valid scores vs zero/null.

Detects anomalies in score coverage and generates structured report.
"""

from typing import Any

import structlog

logger = structlog.get_logger()


class ScoreParityAnalyzer:
    """Analyzes score coverage across trades and signals."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def analyze(self) -> dict[str, Any]:
        """Run full score parity analysis."""
        from sqlalchemy import text

        result: dict[str, Any] = {
            "trade_scores": {},
            "signal_scores": {},
            "anomalies": [],
            "coverage_pct": 0.0,
            "status": "BROKEN",
            "details": "",
            # New: per-era coverage
            "global_coverage": {},
            "post_diagnostics_coverage": {},
            "legacy_stats": {},
            "real_status": "BROKEN",
        }

        async with self._sf() as session:
            # ── Global trade outcomes score coverage (all rows) ──
            trade_result = await session.execute(text("""
                SELECT
                    count(*) as total,
                    count(CASE WHEN signal_score IS NOT NULL AND signal_score > 0 THEN 1 END) as has_signal_score,
                    count(CASE WHEN trend_score IS NOT NULL AND trend_score > 0 THEN 1 END) as has_trend_score,
                    count(CASE WHEN micro_score IS NOT NULL AND micro_score > 0 THEN 1 END) as has_micro_score,
                    count(CASE WHEN (signal_score IS NULL OR signal_score = 0)
                                AND (trend_score IS NULL OR trend_score = 0)
                                AND (micro_score IS NULL OR micro_score = 0) THEN 1 END) as all_zero,
                    count(CASE WHEN signal_score IS NOT NULL AND signal_score > 0
                                AND trend_score IS NOT NULL AND trend_score > 0
                                AND micro_score IS NOT NULL AND micro_score > 0 THEN 1 END) as all_present
                FROM trade_outcomes
            """))
            trade_row = trade_result.mappings().first()

            if trade_row and trade_row["total"] > 0:
                total = trade_row["total"]
                result["trade_scores"] = {
                    "total": total,
                    "has_signal_score": trade_row["has_signal_score"],
                    "has_trend_score": trade_row["has_trend_score"],
                    "has_micro_score": trade_row["has_micro_score"],
                    "all_zero": trade_row["all_zero"],
                    "all_present": trade_row["all_present"],
                    "signal_score_pct": round(trade_row["has_signal_score"] / total * 100, 1),
                    "trend_score_pct": round(trade_row["has_trend_score"] / total * 100, 1),
                    "micro_score_pct": round(trade_row["has_micro_score"] / total * 100, 1),
                    "all_present_pct": round(trade_row["all_present"] / total * 100, 1),
                    "all_zero_pct": round(trade_row["all_zero"] / total * 100, 1),
                }

            # ── Global signal evaluations score coverage (all rows) ──
            signal_result = await session.execute(text("""
                SELECT
                    count(*) as total,
                    count(CASE WHEN signal_score IS NOT NULL AND signal_score > 0 THEN 1 END) as has_signal_score,
                    count(CASE WHEN trend_score IS NOT NULL AND trend_score > 0 THEN 1 END) as has_trend_score,
                    count(CASE WHEN micro_score IS NOT NULL AND micro_score > 0 THEN 1 END) as has_micro_score,
                    count(CASE WHEN (signal_score IS NULL OR signal_score = 0)
                                AND (trend_score IS NULL OR trend_score = 0)
                                AND (micro_score IS NULL OR micro_score = 0) THEN 1 END) as all_zero,
                    count(CASE WHEN signal_score IS NOT NULL AND signal_score > 0
                                AND trend_score IS NOT NULL AND trend_score > 0
                                AND micro_score IS NOT NULL AND micro_score > 0 THEN 1 END) as all_present
                FROM signal_evaluations
            """))
            signal_row = signal_result.mappings().first()

            if signal_row and signal_row["total"] > 0:
                total = signal_row["total"]
                result["signal_scores"] = {
                    "total": total,
                    "has_signal_score": signal_row["has_signal_score"],
                    "has_trend_score": signal_row["has_trend_score"],
                    "has_micro_score": signal_row["has_micro_score"],
                    "all_zero": signal_row["all_zero"],
                    "all_present": signal_row["all_present"],
                    "signal_score_pct": round(signal_row["has_signal_score"] / total * 100, 1),
                    "trend_score_pct": round(signal_row["has_trend_score"] / total * 100, 1),
                    "micro_score_pct": round(signal_row["has_micro_score"] / total * 100, 1),
                    "all_present_pct": round(signal_row["all_present"] / total * 100, 1),
                    "all_zero_pct": round(signal_row["all_zero"] / total * 100, 1),
                }

            # ── Post-diagnostics trades (entry_quality_label IS NOT NULL) ──
            pd_trade_result = await session.execute(text("""
                SELECT
                    count(*) as total,
                    count(CASE WHEN signal_score IS NOT NULL AND signal_score > 0 THEN 1 END) as has_signal_score,
                    count(CASE WHEN trend_score IS NOT NULL AND trend_score > 0 THEN 1 END) as has_trend_score,
                    count(CASE WHEN micro_score IS NOT NULL AND micro_score > 0 THEN 1 END) as has_micro_score,
                    count(CASE WHEN config_snapshot IS NOT NULL THEN 1 END) as has_snapshot,
                    count(CASE WHEN signal_score IS NOT NULL AND signal_score > 0
                                AND trend_score IS NOT NULL AND trend_score > 0
                                AND micro_score IS NOT NULL AND micro_score > 0 THEN 1 END) as all_present
                FROM trade_outcomes
                WHERE entry_quality_label IS NOT NULL
            """))
            pd_trade_row = pd_trade_result.mappings().first()

            # ── Post-diagnostics signals (join to trade_outcomes via signal_id or standalone check) ──
            pd_signal_result = await session.execute(text("""
                SELECT
                    count(*) as total,
                    count(CASE WHEN signal_score IS NOT NULL AND signal_score > 0 THEN 1 END) as has_signal_score,
                    count(CASE WHEN trend_score IS NOT NULL AND trend_score > 0 THEN 1 END) as has_trend_score,
                    count(CASE WHEN micro_score IS NOT NULL AND micro_score > 0 THEN 1 END) as has_micro_score,
                    count(CASE WHEN signal_score IS NOT NULL AND signal_score > 0
                                AND trend_score IS NOT NULL AND trend_score > 0
                                AND micro_score IS NOT NULL AND micro_score > 0 THEN 1 END) as all_present
                FROM signal_evaluations
                WHERE entry_quality_label IS NOT NULL
            """))
            pd_signal_row = pd_signal_result.mappings().first()

            # ── Legacy counts ──
            legacy_trade_result = await session.execute(text("""
                SELECT count(*) as total FROM trade_outcomes WHERE entry_quality_label IS NULL
            """))
            legacy_trade_row = legacy_trade_result.mappings().first()

            legacy_signal_result = await session.execute(text("""
                SELECT count(*) as total FROM signal_evaluations WHERE entry_quality_label IS NULL
            """))
            legacy_signal_row = legacy_signal_result.mappings().first()

        # ── Build global coverage block ──
        trade_s = result["trade_scores"]
        signal_s = result["signal_scores"]
        global_trade_pct = trade_s.get("all_present_pct", 0) if trade_s.get("total", 0) > 0 else 0
        global_signal_pct = signal_s.get("all_present_pct", 0) if signal_s.get("total", 0) > 0 else 0
        global_coverages = [p for p, t in [(global_trade_pct, trade_s.get("total", 0)),
                                            (global_signal_pct, signal_s.get("total", 0))] if t > 0]
        global_avg = round(sum(global_coverages) / len(global_coverages), 1) if global_coverages else 0
        result["global_coverage"] = {
            "trade_coverage_pct": global_trade_pct,
            "signal_coverage_pct": global_signal_pct,
            "details": {
                "trade_scores": trade_s,
                "signal_scores": signal_s,
            },
        }

        # ── Build post-diagnostics coverage block ──
        pd_trade_total = pd_trade_row["total"] if pd_trade_row else 0
        pd_signal_total = pd_signal_row["total"] if pd_signal_row else 0

        pd_trade_pct = 0.0
        pd_signal_pct = 0.0
        pd_trade_details: dict[str, Any] = {}
        pd_signal_details: dict[str, Any] = {}

        if pd_trade_row and pd_trade_total > 0:
            t = pd_trade_total
            pd_trade_pct = round(pd_trade_row["all_present"] / t * 100, 1)
            pd_trade_details = {
                "total": t,
                "has_signal_score": pd_trade_row["has_signal_score"],
                "has_trend_score": pd_trade_row["has_trend_score"],
                "has_micro_score": pd_trade_row["has_micro_score"],
                "has_snapshot": pd_trade_row["has_snapshot"],
                "all_present": pd_trade_row["all_present"],
                "signal_score_pct": round(pd_trade_row["has_signal_score"] / t * 100, 1),
                "trend_score_pct": round(pd_trade_row["has_trend_score"] / t * 100, 1),
                "micro_score_pct": round(pd_trade_row["has_micro_score"] / t * 100, 1),
                "snapshot_pct": round(pd_trade_row["has_snapshot"] / t * 100, 1),
                "all_present_pct": pd_trade_pct,
            }

        if pd_signal_row and pd_signal_total > 0:
            s = pd_signal_total
            pd_signal_pct = round(pd_signal_row["all_present"] / s * 100, 1)
            pd_signal_details = {
                "total": s,
                "has_signal_score": pd_signal_row["has_signal_score"],
                "has_trend_score": pd_signal_row["has_trend_score"],
                "has_micro_score": pd_signal_row["has_micro_score"],
                "all_present": pd_signal_row["all_present"],
                "signal_score_pct": round(pd_signal_row["has_signal_score"] / s * 100, 1),
                "trend_score_pct": round(pd_signal_row["has_trend_score"] / s * 100, 1),
                "micro_score_pct": round(pd_signal_row["has_micro_score"] / s * 100, 1),
                "all_present_pct": pd_signal_pct,
            }

        result["post_diagnostics_coverage"] = {
            "trade_count": pd_trade_total,
            "trade_coverage_pct": pd_trade_pct,
            "signal_count": pd_signal_total,
            "signal_coverage_pct": pd_signal_pct,
            "details": {
                "trade_scores": pd_trade_details,
                "signal_scores": pd_signal_details,
            },
        }

        # ── Legacy stats ──
        legacy_trades = legacy_trade_row["total"] if legacy_trade_row else 0
        legacy_signals = legacy_signal_row["total"] if legacy_signal_row else 0
        result["legacy_stats"] = {
            "legacy_trades": legacy_trades,
            "legacy_signals": legacy_signals,
            "note": "Trades/signals created before entry diagnostics were implemented",
        }

        # Detect anomalies
        anomalies = []

        if trade_s.get("all_zero_pct", 100) > 20:
            anomalies.append({
                "code": "ALL_ZERO_SCORES",
                "severity": "critical",
                "message": f"{trade_s['all_zero_pct']}% of trades have all scores = 0 or NULL",
                "source": "trade_outcomes",
            })

        if trade_s.get("micro_score_pct", 0) < 50 and trade_s.get("total", 0) > 0:
            anomalies.append({
                "code": "MISSING_MICRO_SCORE",
                "severity": "warning",
                "message": f"Only {trade_s.get('micro_score_pct', 0)}% of trades have micro_score > 0",
                "source": "trade_outcomes",
            })

        if trade_s.get("trend_score_pct", 0) < 50 and trade_s.get("total", 0) > 0:
            anomalies.append({
                "code": "MISSING_TREND_SCORE",
                "severity": "warning",
                "message": f"Only {trade_s.get('trend_score_pct', 0)}% of trades have trend_score > 0",
                "source": "trade_outcomes",
            })

        if trade_s.get("signal_score_pct", 0) < 50 and trade_s.get("total", 0) > 0:
            anomalies.append({
                "code": "MISSING_SIGNAL_SCORE",
                "severity": "critical",
                "message": f"Only {trade_s.get('signal_score_pct', 0)}% of trades have signal_score > 0",
                "source": "trade_outcomes",
            })

        if signal_s.get("all_zero_pct", 100) > 30:
            anomalies.append({
                "code": "ALL_ZERO_SIGNAL_EVAL",
                "severity": "warning",
                "message": f"{signal_s.get('all_zero_pct', 0)}% of signal evaluations have all scores = 0",
                "source": "signal_evaluations",
            })

        # Parity check between trades and signals
        if trade_s.get("total", 0) > 0 and signal_s.get("total", 0) > 0:
            trade_coverage = trade_s.get("all_present_pct", 0)
            signal_coverage = signal_s.get("all_present_pct", 0)
            if abs(trade_coverage - signal_coverage) > 20:
                anomalies.append({
                    "code": "SCORE_PARITY_MISMATCH",
                    "severity": "warning",
                    "message": f"Trade score coverage ({trade_coverage}%) differs from signal coverage ({signal_coverage}%) by > 20pp",
                    "source": "cross_table",
                })

        result["anomalies"] = anomalies

        # Overall global coverage = average of trade all_present_pct and signal all_present_pct
        coverage = global_avg
        result["coverage_pct"] = coverage

        # Overall post-diagnostics coverage
        pd_coverages = [p for p, t in [(pd_trade_pct, pd_trade_total),
                                        (pd_signal_pct, pd_signal_total)] if t > 0]
        pd_coverage = round(sum(pd_coverages) / len(pd_coverages), 1) if pd_coverages else 0

        # Status (global — backward compat)
        critical_count = sum(1 for a in anomalies if a["severity"] == "critical")
        warning_count = sum(1 for a in anomalies if a["severity"] == "warning")

        if coverage >= 80 and critical_count == 0:
            result["status"] = "OK"
            result["details"] = f"Score coverage {coverage}% is healthy"
        elif coverage >= 50 and critical_count == 0:
            result["status"] = "DEGRADED"
            result["details"] = f"Score coverage {coverage}% is below optimal, {warning_count} warnings"
        else:
            result["status"] = "BROKEN"
            result["details"] = f"Score coverage {coverage}%, {critical_count} critical issues, {warning_count} warnings"

        if trade_s.get("total", 0) == 0 and signal_s.get("total", 0) == 0:
            result["status"] = "NO_DATA"
            result["details"] = "No trades or signals found in database"

        # real_status — based on post-diagnostics data only
        if pd_trade_total == 0 and pd_signal_total == 0:
            result["real_status"] = "NO_DATA"
        elif pd_coverage >= 80 and critical_count == 0:
            result["real_status"] = "OK"
        elif pd_coverage >= 50 and critical_count == 0:
            result["real_status"] = "DEGRADED"
        else:
            result["real_status"] = "BROKEN"

        logger.info("score_parity.analyzed", status=result["status"], real_status=result["real_status"],
                     coverage=coverage, pd_coverage=pd_coverage, anomalies=len(anomalies))

        return result
