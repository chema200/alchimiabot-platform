"""Data Quality Check: validates data integrity and completeness."""

from sqlalchemy import text

from ..base import AuditCheck, CheckResult


class DataQualityCheck(AuditCheck):
    name = "data_quality"
    audit_type = "data_quality"

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def run(self) -> CheckResult:
        result = CheckResult(summary="Data quality and completeness check")

        # 1. Trades with impossible PnL (net > gross)
        async with self._sf() as s:
            r = await s.execute(text(
                "SELECT count(*) FROM trade_outcomes WHERE net_pnl > gross_pnl AND gross_pnl != 0"))
            impossible = r.scalar() or 0
            result.metrics["impossible_pnl"] = impossible
            if impossible > 0:
                result.add_finding("error", "NET_GT_GROSS",
                    f"{impossible} trades with net PnL > gross PnL (impossible)")

        # 2. Trades with null/empty fields
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT count(*) FROM trade_outcomes
                WHERE coin IS NULL OR side IS NULL OR entry_price IS NULL
                    OR exit_price IS NULL OR entry_time IS NULL"""))
            nulls = r.scalar() or 0
            result.metrics["null_fields"] = nulls
            if nulls > 0:
                result.add_finding("warning", "NULL_FIELDS", f"{nulls} trades with null required fields")

        # 3. Signals with missing scores
        async with self._sf() as s:
            r = await s.execute(text(
                "SELECT count(*) FROM signal_evaluations WHERE signal_score IS NULL OR signal_score = 0"))
            no_score = r.scalar() or 0
            result.metrics["signals_no_score"] = no_score

        # 4. Timestamps in the future
        async with self._sf() as s:
            r = await s.execute(text(
                "SELECT count(*) FROM trade_outcomes WHERE entry_time > now() + interval '5 minutes'"))
            future_ts = r.scalar() or 0
            result.metrics["future_timestamps"] = future_ts
            if future_ts > 0:
                result.add_finding("warning", "FUTURE_TIMESTAMPS", f"{future_ts} trades with future timestamps")

        # 5. Feature snapshots completeness
        async with self._sf() as s:
            r = await s.execute(text(
                "SELECT count(*) FROM feature_snapshots WHERE timestamp > now() - interval '1 hour'"))
            recent_snaps = r.scalar() or 0
            result.metrics["snapshots_last_hour"] = recent_snaps
            if recent_snaps == 0:
                result.add_finding("warning", "NO_RECENT_SNAPSHOTS", "No feature snapshots in last hour")

        # 6. Outlier trades (PnL > $10 or < -$10 for small positions)
        async with self._sf() as s:
            r = await s.execute(text(
                "SELECT count(*) FROM trade_outcomes WHERE abs(net_pnl) > 10"))
            outliers = r.scalar() or 0
            result.metrics["pnl_outliers"] = outliers
            if outliers > 0:
                result.add_finding("info", "PNL_OUTLIERS", f"{outliers} trades with |PnL| > $10")

        return result
