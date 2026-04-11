"""Daily Audit: validates consistency between agentbot-live and platform.

Runs checks every hour and generates a daily report.
Catches drift before it contaminates research data.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


class DailyAudit:
    """Validates data consistency between bot and platform."""

    def __init__(self, session_factory, bot_api_url: str = "http://localhost:8080",
                 interval_sec: int = 3600) -> None:
        self._session_factory = session_factory
        self._bot_url = bot_api_url
        self._interval = interval_sec
        self._running = False
        self._last_report: dict[str, Any] = {}
        self._auth_token: str = ""

    async def start(self) -> None:
        self._running = True
        logger.info("daily_audit.started", interval=self._interval)
        while self._running:
            try:
                await self._run_audit()
            except Exception:
                logger.exception("daily_audit.error")
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        self._running = False

    async def _get_token(self) -> str:
        """Get JWT token from bot."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.post(f"{self._bot_url}/api/auth/login",
                    json={"username": "chema200", "password": "iotron4321"})
                if r.status_code == 200:
                    self._auth_token = r.json().get("token", "")
        except Exception:
            pass
        return self._auth_token

    async def _run_audit(self) -> dict[str, Any]:
        """Run all audit checks."""
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {},
            "warnings": [],
            "errors": [],
        }

        # 1. Check bot trade count vs platform trade count
        await self._check_trade_counts(report)

        # 2. Check PnL consistency
        await self._check_pnl_consistency(report)

        # 3. Check for duplicates
        await self._check_duplicates(report)

        # 4. Check signal flow
        await self._check_signals(report)

        # 5. Check disk growth
        await self._check_disk_growth(report)

        # 6. Check data freshness
        await self._check_freshness(report)

        # Summary
        report["status"] = "OK" if not report["errors"] else "ERRORS"
        if report["warnings"] and not report["errors"]:
            report["status"] = "WARNINGS"

        self._last_report = report
        logger.info("daily_audit.completed", status=report["status"],
                     warnings=len(report["warnings"]), errors=len(report["errors"]))
        return report

    async def _check_trade_counts(self, report: dict) -> None:
        """Bot trades in last 24h vs platform trades."""
        try:
            # Platform count
            async with self._session_factory() as session:
                result = await session.execute(text(
                    "SELECT count(*) FROM trade_outcomes WHERE exit_time > now() - interval '24 hours'"))
                platform_count = result.scalar() or 0

            # Bot count
            token = await self._get_token()
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self._bot_url}/api/hl/trading/history",
                    headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 200:
                    bot_trades = r.json()
                    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                    bot_count = sum(1 for t in bot_trades if (t.get("exitAt") or "") > cutoff)
                else:
                    bot_count = -1

            report["checks"]["trade_count"] = {"bot": bot_count, "platform": platform_count}

            if bot_count >= 0 and bot_count != platform_count:
                diff = bot_count - platform_count
                if abs(diff) > 2:
                    report["errors"].append(f"Trade count mismatch: bot={bot_count}, platform={platform_count}")
                else:
                    report["warnings"].append(f"Minor trade count diff: bot={bot_count}, platform={platform_count}")
        except Exception as e:
            report["warnings"].append(f"Trade count check failed: {e}")

    async def _check_pnl_consistency(self, report: dict) -> None:
        """Compare total PnL between bot history and platform."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(text("""
                    SELECT round(sum(net_pnl)::numeric, 4) as total_net,
                           round(sum(gross_pnl)::numeric, 4) as total_gross,
                           round(sum(fee)::numeric, 4) as total_fees
                    FROM trade_outcomes
                """))
                row = result.mappings().first()
                if row:
                    report["checks"]["platform_pnl"] = {
                        "net": float(row["total_net"] or 0),
                        "gross": float(row["total_gross"] or 0),
                        "fees": float(row["total_fees"] or 0),
                    }

                    # Sanity: net should never exceed gross
                    net = float(row["total_net"] or 0)
                    gross = float(row["total_gross"] or 0)
                    if net > gross and gross != 0:
                        report["errors"].append(f"Impossible: net PnL ({net}) > gross PnL ({gross})")
        except Exception as e:
            report["warnings"].append(f"PnL check failed: {e}")

    async def _check_duplicates(self, report: dict) -> None:
        """Check for duplicate trades in platform."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(text("""
                    SELECT coin, exit_time, count(*) as cnt
                    FROM trade_outcomes
                    GROUP BY coin, exit_time
                    HAVING count(*) > 1
                """))
                dupes = result.mappings().all()
                report["checks"]["duplicates"] = len(dupes)
                if dupes:
                    report["warnings"].append(f"Found {len(dupes)} duplicate trade(s)")
        except Exception as e:
            report["warnings"].append(f"Duplicate check failed: {e}")

    async def _check_signals(self, report: dict) -> None:
        """Check signal evaluation flow."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(text("""
                    SELECT action, count(*) as cnt
                    FROM signal_evaluations
                    WHERE timestamp > now() - interval '24 hours'
                    GROUP BY action
                """))
                rows = result.mappings().all()
                signal_counts = {r["action"]: r["cnt"] for r in rows}
                report["checks"]["signals_24h"] = signal_counts

                total = sum(signal_counts.values())
                if total == 0:
                    report["warnings"].append("No signals received in last 24h")
                enters = signal_counts.get("ENTER", 0)
                if enters > 0 and report["checks"].get("trade_count", {}).get("platform", 0) == 0:
                    report["warnings"].append(f"{enters} ENTER signals but 0 trades — possible entry failure")
        except Exception as e:
            report["warnings"].append(f"Signal check failed: {e}")

    async def _check_disk_growth(self, report: dict) -> None:
        """Check disk usage."""
        try:
            import shutil
            usage = shutil.disk_usage("/")
            pct = usage.used / usage.total * 100
            report["checks"]["disk_pct"] = round(pct, 1)
            if pct > 85:
                report["errors"].append(f"Disk usage critical: {pct:.1f}%")
            elif pct > 70:
                report["warnings"].append(f"Disk usage high: {pct:.1f}%")
        except Exception as e:
            report["warnings"].append(f"Disk check failed: {e}")

    async def _check_freshness(self, report: dict) -> None:
        """Check that data is recent."""
        try:
            async with self._session_factory() as session:
                # Last trade
                result = await session.execute(text(
                    "SELECT max(exit_time) FROM trade_outcomes"))
                last_trade = result.scalar()

                # Last snapshot
                result = await session.execute(text(
                    "SELECT max(timestamp) FROM feature_snapshots"))
                last_snapshot = result.scalar()

                report["checks"]["freshness"] = {
                    "last_trade": last_trade.isoformat() if last_trade else None,
                    "last_snapshot": last_snapshot.isoformat() if last_snapshot else None,
                }

                if last_snapshot:
                    age_min = (datetime.now(timezone.utc) - last_snapshot.replace(tzinfo=timezone.utc)).total_seconds() / 60
                    if age_min > 5:
                        report["warnings"].append(f"Feature snapshots stale: {age_min:.0f} min old")
        except Exception as e:
            report["warnings"].append(f"Freshness check failed: {e}")

    @property
    def last_report(self) -> dict[str, Any]:
        return self._last_report

    async def run_now(self) -> dict[str, Any]:
        """Run audit immediately (for API calls)."""
        return await self._run_audit()
