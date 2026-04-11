"""Daily Report Generator: auto-fills the complete audit template.

Collects data from all sources (bot API, platform DB, system metrics)
and generates a structured daily report. Persists to DB and serves via API.
"""

import asyncio
import os
import shutil
import psutil
from datetime import datetime, timezone, timedelta, date
from typing import Any

import httpx
import structlog
from sqlalchemy import text

logger = structlog.get_logger()


class DailyReportGenerator:
    """Generates the complete daily audit report automatically."""

    def __init__(self, session_factory, bot_url: str = "http://localhost:8080") -> None:
        self._sf = session_factory
        self._bot_url = bot_url

    async def generate(self, report_date: date | None = None) -> dict[str, Any]:
        """Generate full daily report for a given date (default: today)."""
        d = report_date or date.today()
        start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)

        report = {
            "date": d.isoformat(),
            "day_of_week": d.strftime("%A"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Gather all sections in parallel
        sections = await asyncio.gather(
            self._general_info(start, end),
            self._system_status(),
            self._integration(start, end),
            self._pnl_consistency(start, end),
            self._signals(start, end),
            self._features(),
            self._regimes(),
            self._storage(),
            self._trading_analysis(start, end),
            self._problems(start, end),
            return_exceptions=True,
        )

        keys = ["general", "system", "integration", "pnl", "signals",
                "features", "regimes", "storage", "trading", "problems"]
        for key, result in zip(keys, sections):
            report[key] = result if not isinstance(result, Exception) else {"error": str(result)}

        # Calculate scores
        report["scores"] = self._calculate_scores(report)

        # Persist to DB
        await self._persist(report)

        return report

    async def _general_info(self, start: datetime, end: datetime) -> dict:
        """Section 1: General info — mode, trades, PnL."""
        result = {"mode": "?", "hours_operating": 0, "trades": 0,
                  "total_pnl": 0, "total_fees": 0, "net_pnl": 0}

        # From platform DB
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT count(*) as trades,
                       coalesce(sum(gross_pnl), 0) as gross,
                       coalesce(sum(fee), 0) as fees,
                       coalesce(sum(net_pnl), 0) as net,
                       mode
                FROM trade_outcomes
                WHERE exit_time >= :start AND exit_time < :end
                GROUP BY mode ORDER BY count(*) DESC LIMIT 1
            """), {"start": start, "end": end})
            row = r.mappings().first()
            if row:
                result["trades"] = int(row["trades"])
                result["total_pnl"] = round(float(row["gross"]), 4)
                result["total_fees"] = round(float(row["fees"]), 4)
                result["net_pnl"] = round(float(row["net"]), 4)
                result["mode"] = row["mode"] or "?"

            # Total trades (all modes)
            r2 = await s.execute(text("""
                SELECT count(*) as total,
                       coalesce(sum(gross_pnl), 0) as gross,
                       coalesce(sum(fee), 0) as fees,
                       coalesce(sum(net_pnl), 0) as net
                FROM trade_outcomes
                WHERE exit_time >= :start AND exit_time < :end
            """), {"start": start, "end": end})
            row2 = r2.mappings().first()
            if row2:
                result["trades"] = int(row2["total"])
                result["total_pnl"] = round(float(row2["gross"]), 4)
                result["total_fees"] = round(float(row2["fees"]), 4)
                result["net_pnl"] = round(float(row2["net"]), 4)

        return result

    async def _system_status(self) -> dict:
        """Section 2: System health."""
        result = {"bot_running": False, "platform_running": True,
                  "ws_stable": False, "cpu_pct": 0, "ram_pct": 0, "issues": []}

        # Check bot
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.post(f"{self._bot_url}/api/auth/login",
                    json={"username": "chema200", "password": "iotron4321"})
                result["bot_running"] = r.status_code == 200
                if r.status_code == 200:
                    token = r.json().get("token", "")
                    r2 = await c.get(f"{self._bot_url}/api/hl/status",
                        headers={"Authorization": f"Bearer {token}"})
                    if r2.status_code == 200:
                        data = r2.json()
                        result["ws_stable"] = data.get("connected", False)
                        result["hl_connected"] = data.get("connected", False)
                        result["assets_loaded"] = data.get("assetsLoaded", 0)
        except Exception:
            result["issues"].append("Cannot reach bot API")

        # System resources
        result["cpu_pct"] = round(psutil.cpu_percent(interval=1), 1)
        result["ram_pct"] = round(psutil.virtual_memory().percent, 1)

        if result["cpu_pct"] > 80:
            result["issues"].append(f"High CPU: {result['cpu_pct']}%")
        if result["ram_pct"] > 85:
            result["issues"].append(f"High RAM: {result['ram_pct']}%")

        return result

    async def _integration(self, start: datetime, end: datetime) -> dict:
        """Section 3: Integration live → platform."""
        result = {"bot_trades": 0, "platform_trades": 0, "diff_trades": 0,
                  "signals_received": 0, "duplicates": 0, "issues": []}

        # Platform trades
        async with self._sf() as s:
            r = await s.execute(text(
                "SELECT count(*) FROM trade_outcomes WHERE exit_time >= :s AND exit_time < :e"),
                {"s": start, "e": end})
            result["platform_trades"] = r.scalar() or 0

            r = await s.execute(text(
                "SELECT count(*) FROM signal_evaluations WHERE timestamp >= :s AND timestamp < :e"),
                {"s": start, "e": end})
            result["signals_received"] = r.scalar() or 0

            r = await s.execute(text("""
                SELECT count(*) FROM (
                    SELECT coin, exit_time FROM trade_outcomes
                    WHERE exit_time >= :s AND exit_time < :e
                    GROUP BY coin, exit_time HAVING count(*) > 1
                ) d"""), {"s": start, "e": end})
            result["duplicates"] = r.scalar() or 0

        # Bot trades
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(f"{self._bot_url}/api/auth/login",
                    json={"username": "chema200", "password": "iotron4321"})
                if r.status_code == 200:
                    token = r.json()["token"]
                    r2 = await c.get(f"{self._bot_url}/api/hl/trading/history",
                        headers={"Authorization": f"Bearer {token}"})
                    if r2.status_code == 200:
                        trades = r2.json()
                        s_iso = start.isoformat()
                        e_iso = end.isoformat()
                        result["bot_trades"] = sum(1 for t in trades
                            if s_iso <= (t.get("exitAt") or "") < e_iso)
        except Exception:
            pass

        result["diff_trades"] = result["bot_trades"] - result["platform_trades"]
        if abs(result["diff_trades"]) > 2:
            result["issues"].append(f"Trade count mismatch: {result['diff_trades']}")
        if result["duplicates"] > 0:
            result["issues"].append(f"{result['duplicates']} duplicate trades")

        return result

    async def _pnl_consistency(self, start: datetime, end: datetime) -> dict:
        """Section 4: PnL and consistency."""
        result = {"wins": 0, "losses": 0, "win_rate": 0, "profit_factor": 0,
                  "expectancy": 0, "avg_win": 0, "avg_loss": 0, "issues": []}

        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT
                    count(*) as total,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    sum(case when net_pnl <= 0 then 1 else 0 end) as losses,
                    coalesce(avg(case when net_pnl > 0 then net_pnl end), 0) as avg_win,
                    coalesce(avg(case when net_pnl <= 0 then net_pnl end), 0) as avg_loss,
                    coalesce(sum(case when net_pnl > 0 then net_pnl else 0 end), 0) as total_wins,
                    coalesce(sum(case when net_pnl < 0 then net_pnl else 0 end), 0) as total_losses,
                    sum(case when gross_pnl > 0 and net_pnl <= 0 then 1 else 0 end) as fee_killed
                FROM trade_outcomes
                WHERE exit_time >= :s AND exit_time < :e
            """), {"s": start, "e": end})
            row = r.mappings().first()
            if row and row["total"] > 0:
                total = int(row["total"])
                result["wins"] = int(row["wins"] or 0)
                result["losses"] = int(row["losses"] or 0)
                result["win_rate"] = round(result["wins"] / total * 100, 1)
                result["avg_win"] = round(float(row["avg_win"] or 0), 4)
                result["avg_loss"] = round(float(row["avg_loss"] or 0), 4)
                result["expectancy"] = round(float(row["avg_win"] or 0) * result["wins"] / total +
                                             float(row["avg_loss"] or 0) * result["losses"] / total, 4)
                gross_loss = abs(float(row["total_losses"] or 0))
                result["profit_factor"] = round(float(row["total_wins"] or 0) / gross_loss, 2) if gross_loss > 0 else 0
                result["fee_killed"] = int(row["fee_killed"] or 0)

                if result["fee_killed"] > 0:
                    result["issues"].append(f"{result['fee_killed']} trades killed by fees (gross+ net-)")
                if result["win_rate"] < 35:
                    result["issues"].append(f"Low win rate: {result['win_rate']}%")

        return result

    async def _signals(self, start: datetime, end: datetime) -> dict:
        """Section 5: Signal evaluations."""
        result = {"enter": 0, "skip": 0, "blocked": 0, "issues": []}

        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT action, count(*) as cnt
                FROM signal_evaluations
                WHERE timestamp >= :s AND timestamp < :e
                GROUP BY action
            """), {"s": start, "e": end})
            for row in r.mappings().all():
                action = row["action"].lower() if row["action"] else "unknown"
                if action in result:
                    result[action] = int(row["cnt"])

        total = result["enter"] + result["skip"] + result["blocked"]
        if total > 0:
            enter_pct = result["enter"] / total * 100
            if enter_pct > 50:
                result["issues"].append(f"Overtrading: {enter_pct:.0f}% of signals entered")
            if enter_pct < 5 and total > 20:
                result["issues"].append(f"Infratrading: only {enter_pct:.0f}% entered")

        return result

    async def _features(self) -> dict:
        """Section 6: Feature health."""
        result = {"snapshots_today": 0, "coins_with_features": 0, "issues": []}

        async with self._sf() as s:
            r = await s.execute(text(
                "SELECT count(*) FROM feature_snapshots WHERE timestamp > now() - interval '24 hours'"))
            result["snapshots_today"] = r.scalar() or 0

            r = await s.execute(text(
                "SELECT count(distinct coin) FROM feature_snapshots WHERE timestamp > now() - interval '1 hour'"))
            result["coins_with_features"] = r.scalar() or 0

        if result["snapshots_today"] == 0:
            result["issues"].append("No feature snapshots today")

        return result

    async def _regimes(self) -> dict:
        """Section 7: Market regimes."""
        result = {"dominant_regime": "?", "regime_changes": 0, "issues": []}

        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT regime, count(*) as cnt
                FROM regime_labels
                WHERE timestamp > now() - interval '24 hours'
                GROUP BY regime ORDER BY cnt DESC LIMIT 1
            """))
            row = r.mappings().first()
            if row:
                result["dominant_regime"] = row["regime"]

        return result

    async def _storage(self) -> dict:
        """Section 8: Storage health."""
        usage = shutil.disk_usage("/")
        pct = usage.used / usage.total * 100

        # Count parquet files today
        today_str = date.today().isoformat()
        parquet_today = 0
        if os.path.exists("data/raw"):
            for root, _, files in os.walk("data/raw"):
                if today_str in root:
                    parquet_today += sum(1 for f in files if f.endswith(".parquet"))

        result = {
            "disk_used_gb": round(usage.used / 1e9, 2),
            "disk_free_gb": round(usage.free / 1e9, 2),
            "disk_pct": round(pct, 1),
            "parquet_files_today": parquet_today,
            "issues": [],
        }

        if pct > 85:
            result["issues"].append(f"Disk critical: {pct:.1f}%")
        elif pct > 70:
            result["issues"].append(f"Disk high: {pct:.1f}%")

        return result

    async def _trading_analysis(self, start: datetime, end: datetime) -> dict:
        """Section 9: Trading analysis — best/worst coins, hours."""
        result = {"best_coins": [], "worst_coins": [], "best_hours": [], "worst_hours": [], "by_exit_reason": []}

        async with self._sf() as s:
            # Best/worst coins
            r = await s.execute(text("""
                SELECT coin, count(*) as trades,
                    round(sum(net_pnl)::numeric, 4) as pnl,
                    round(sum(case when net_pnl > 0 then 1.0 else 0 end) / count(*)::numeric * 100, 0) as wr
                FROM trade_outcomes WHERE exit_time >= :s AND exit_time < :e
                GROUP BY coin HAVING count(*) >= 2 ORDER BY pnl DESC
            """), {"s": start, "e": end})
            rows = [dict(r) for r in r.mappings().all()]
            result["best_coins"] = rows[:5]
            result["worst_coins"] = rows[-5:] if len(rows) > 5 else []

            # Best/worst hours
            r = await s.execute(text("""
                SELECT extract(hour from entry_time)::int as hour,
                    count(*) as trades,
                    round(sum(net_pnl)::numeric, 4) as pnl
                FROM trade_outcomes WHERE exit_time >= :s AND exit_time < :e
                GROUP BY hour ORDER BY pnl DESC
            """), {"s": start, "e": end})
            hours = [dict(r) for r in r.mappings().all()]
            result["best_hours"] = hours[:3]
            result["worst_hours"] = hours[-3:] if len(hours) > 3 else []

            # By exit reason
            r = await s.execute(text("""
                SELECT exit_reason, count(*) as trades,
                    round(sum(net_pnl)::numeric, 4) as pnl
                FROM trade_outcomes WHERE exit_time >= :s AND exit_time < :e
                GROUP BY exit_reason ORDER BY trades DESC
            """), {"s": start, "e": end})
            result["by_exit_reason"] = [dict(r) for r in r.mappings().all()]

        return result

    async def _problems(self, start: datetime, end: datetime) -> dict:
        """Section 10: Aggregate all problems from all sections."""
        # This is filled after all sections are generated
        return {"note": "See issues in each section"}

    def _calculate_scores(self, report: dict) -> dict:
        """Calculate quality scores 0-10."""
        scores = {}

        # Data quality: based on issues found
        data_issues = sum(len(s.get("issues", [])) for s in report.values() if isinstance(s, dict))
        scores["data_quality"] = max(0, 10 - data_issues)

        # Trading quality: based on win rate and profit factor
        pnl = report.get("pnl", {})
        wr = pnl.get("win_rate", 0)
        if wr >= 55:
            scores["trading"] = 8
        elif wr >= 45:
            scores["trading"] = 6
        elif wr >= 35:
            scores["trading"] = 4
        else:
            scores["trading"] = 2

        pf = pnl.get("profit_factor", 0)
        if pf > 1.5:
            scores["trading"] = min(10, scores["trading"] + 2)
        elif pf > 1.0:
            scores["trading"] = min(10, scores["trading"] + 1)

        # System confidence
        sys = report.get("system", {})
        scores["system"] = 10
        if not sys.get("bot_running"):
            scores["system"] -= 5
        if not sys.get("ws_stable"):
            scores["system"] -= 3
        if sys.get("cpu_pct", 0) > 80:
            scores["system"] -= 1
        scores["system"] = max(0, scores["system"])

        # Overall
        scores["overall"] = round(sum(scores.values()) / len(scores), 1)

        return scores

    async def _persist(self, report: dict) -> None:
        """Save report to DB."""
        try:
            async with self._sf() as s:
                await s.execute(text("""
                    INSERT INTO audit_runs (audit_type, status, score, started_at, finished_at, summary, details, metrics)
                    VALUES ('daily_report', :status, :score, :started, now(), :summary, :details, :metrics)
                """), {
                    "status": "OK" if report["scores"]["overall"] >= 6 else "WARNING" if report["scores"]["overall"] >= 4 else "ERROR",
                    "score": int(report["scores"]["overall"] * 10),
                    "started": report["generated_at"],
                    "summary": f"Daily report {report['date']}",
                    "details": str(report)[:5000],
                    "metrics": str(report.get("scores", {})),
                })
                await s.commit()
        except Exception:
            logger.exception("daily_report.persist_error")

    async def get_history(self, days: int = 14) -> list[dict]:
        """Get recent daily reports."""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT started_at, score, status, summary, details
                FROM audit_runs
                WHERE audit_type = 'daily_report'
                ORDER BY started_at DESC LIMIT :n
            """), {"n": days})
            return [dict(row) for row in r.mappings().all()]
