"""Integration Check: validates bot → platform data flow."""

import httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

from ..base import AuditCheck, CheckResult


class IntegrationCheck(AuditCheck):
    name = "integration"
    audit_type = "integration"

    def __init__(self, session_factory, bot_url: str = "http://localhost:8080") -> None:
        self._sf = session_factory
        self._bot_url = bot_url

    async def run(self) -> CheckResult:
        result = CheckResult(summary="Bot → Platform integration check")

        # Get bot trade count (last 24h)
        bot_count = await self._get_bot_trade_count()
        platform_count = await self._get_platform_trade_count()

        result.metrics["bot_trades_24h"] = bot_count
        result.metrics["platform_trades_24h"] = platform_count

        if bot_count < 0:
            result.add_finding("warning", "BOT_UNREACHABLE", "Cannot reach bot API")
        elif bot_count > 0 and platform_count == 0:
            result.add_finding("error", "NO_TRADES_RECEIVED",
                f"Bot has {bot_count} trades but platform received 0")
        elif bot_count >= 0 and abs(bot_count - platform_count) > 3:
            result.add_finding("warning", "TRADE_COUNT_DRIFT",
                f"Bot={bot_count} vs Platform={platform_count} (diff={bot_count - platform_count})")

        # Check signal flow
        signal_count = await self._get_signal_count()
        result.metrics["signals_24h"] = signal_count
        if bot_count > 0 and signal_count == 0:
            result.add_finding("warning", "NO_SIGNALS_RECEIVED",
                "Bot is trading but platform received 0 signals")

        # Check for duplicates
        dupes = await self._check_duplicates()
        result.metrics["duplicates"] = dupes
        if dupes > 0:
            result.add_finding("warning", "DUPLICATE_TRADES", f"Found {dupes} duplicate trades")

        return result

    async def _get_bot_trade_count(self) -> int:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.post(f"{self._bot_url}/api/auth/login",
                    json={"username": "chema200", "password": "iotron4321"})
                if r.status_code != 200:
                    return -1
                token = r.json().get("token", "")
                r = await client.get(f"{self._bot_url}/api/hl/trading/history",
                    headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 200:
                    trades = r.json()
                    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                    return sum(1 for t in trades if (t.get("exitAt") or "") > cutoff)
        except Exception:
            pass
        return -1

    async def _get_platform_trade_count(self) -> int:
        async with self._sf() as s:
            r = await s.execute(text(
                "SELECT count(*) FROM trade_outcomes WHERE exit_time > now() - interval '24 hours'"))
            return r.scalar() or 0

    async def _get_signal_count(self) -> int:
        async with self._sf() as s:
            r = await s.execute(text(
                "SELECT count(*) FROM signal_evaluations WHERE timestamp > now() - interval '24 hours'"))
            return r.scalar() or 0

    async def _check_duplicates(self) -> int:
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT count(*) FROM (
                    SELECT coin, exit_time FROM trade_outcomes
                    GROUP BY coin, exit_time HAVING count(*) > 1
                ) d"""))
            return r.scalar() or 0
