"""Consistency Check: validates PnL/fees/counts match between bot and platform."""

import httpx
from sqlalchemy import text

from ..base import AuditCheck, CheckResult


class ConsistencyCheck(AuditCheck):
    name = "consistency"
    audit_type = "consistency"

    def __init__(self, session_factory, bot_url: str = "http://localhost:8080") -> None:
        self._sf = session_factory
        self._bot_url = bot_url

    async def run(self) -> CheckResult:
        result = CheckResult(summary="Bot vs Platform consistency check")

        # Get bot data
        bot_data = await self._get_bot_data()
        if not bot_data:
            result.add_finding("warning", "BOT_UNREACHABLE", "Cannot reach bot for consistency check")
            return result

        # Get platform data
        platform_data = await self._get_platform_data()

        # Compare
        for field in ["net_pnl", "gross_pnl", "fees"]:
            bot_val = bot_data.get(field, 0)
            plat_val = platform_data.get(field, 0)
            diff = abs(bot_val - plat_val)
            result.metrics[f"bot_{field}"] = bot_val
            result.metrics[f"platform_{field}"] = plat_val
            result.metrics[f"diff_{field}"] = round(diff, 4)

            if diff > 1.0:
                result.add_finding("error", f"{field.upper()}_MISMATCH",
                    f"{field}: bot={bot_val:.4f} vs platform={plat_val:.4f} (diff={diff:.4f})")
            elif diff > 0.1:
                result.add_finding("warning", f"{field.upper()}_DRIFT",
                    f"{field}: bot={bot_val:.4f} vs platform={plat_val:.4f} (diff={diff:.4f})")

        # Win/loss count
        bot_wins = bot_data.get("wins", 0)
        plat_wins = platform_data.get("wins", 0)
        result.metrics["bot_wins"] = bot_wins
        result.metrics["platform_wins"] = plat_wins
        if bot_wins != plat_wins and abs(bot_wins - plat_wins) > 2:
            result.add_finding("warning", "WIN_COUNT_DRIFT",
                f"Wins: bot={bot_wins} vs platform={plat_wins}")

        return result

    async def _get_bot_data(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.post(f"{self._bot_url}/api/auth/login",
                    json={"username": "chema200", "password": "iotron4321"})
                if r.status_code != 200:
                    return None
                token = r.json()["token"]
                r = await client.get(f"{self._bot_url}/api/hl/trading/history",
                    headers={"Authorization": f"Bearer {token}"})
                if r.status_code != 200:
                    return None
                trades = r.json()
                net = sum(t.get("netPnl", 0) for t in trades)
                gross = sum(t.get("grossPnl", 0) for t in trades)
                fees = sum(t.get("fee", 0) for t in trades)
                wins = sum(1 for t in trades if t.get("netPnl", 0) > 0)
                return {"net_pnl": round(net, 4), "gross_pnl": round(gross, 4),
                        "fees": round(fees, 4), "wins": wins, "total": len(trades)}
        except Exception:
            return None

    async def _get_platform_data(self) -> dict:
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT coalesce(sum(net_pnl), 0) as net_pnl,
                       coalesce(sum(gross_pnl), 0) as gross_pnl,
                       coalesce(sum(fee), 0) as fees,
                       coalesce(sum(case when net_pnl > 0 then 1 else 0 end), 0) as wins,
                       count(*) as total
                FROM trade_outcomes"""))
            row = r.mappings().first()
            return {k: round(float(v), 4) if isinstance(v, (int, float)) else int(v)
                    for k, v in dict(row).items()} if row else {}
