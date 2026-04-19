"""Operational Reports: actionable insights from trade data.

These reports answer real trading questions — not academic ML questions.
Each report returns structured data ready for the dashboard.
"""

from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

logger = structlog.get_logger()


class OperationalReports:
    """Generates actionable reports from platform data."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def wr_by_coin(self, user_id: int | None = None) -> list[dict]:
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT coin, count(*) as trades,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    round(sum(case when net_pnl > 0 then 1.0 else 0 end) / count(*)::numeric * 100, 1) as wr,
                    round(sum(net_pnl)::numeric, 4) as total_pnl,
                    round(avg(net_pnl)::numeric, 4) as avg_pnl,
                    round(sum(fee)::numeric, 4) as total_fees
                FROM trade_outcomes {uf_where} GROUP BY coin ORDER BY total_pnl DESC
            """))
            return [dict(row) for row in r.mappings().all()]

    async def wr_by_side(self, user_id: int | None = None) -> list[dict]:
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT side, count(*) as trades,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    round(sum(case when net_pnl > 0 then 1.0 else 0 end) / count(*)::numeric * 100, 1) as wr,
                    round(sum(net_pnl)::numeric, 4) as total_pnl,
                    round(avg(net_pnl)::numeric, 4) as avg_pnl
                FROM trade_outcomes {uf_where} GROUP BY side ORDER BY total_pnl DESC
            """))
            return [dict(row) for row in r.mappings().all()]

    async def wr_by_hour(self, user_id: int | None = None) -> list[dict]:
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT extract(hour from entry_time) as hour,
                    count(*) as trades,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    round(sum(case when net_pnl > 0 then 1.0 else 0 end) / count(*)::numeric * 100, 1) as wr,
                    round(sum(net_pnl)::numeric, 4) as total_pnl
                FROM trade_outcomes {uf_where} GROUP BY hour ORDER BY hour
            """))
            return [dict(row) for row in r.mappings().all()]

    async def pnl_by_mode(self, user_id: int | None = None) -> list[dict]:
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT mode, count(*) as trades,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    round(sum(case when net_pnl > 0 then 1.0 else 0 end) / count(*)::numeric * 100, 1) as wr,
                    round(sum(gross_pnl)::numeric, 4) as total_gross,
                    round(sum(fee)::numeric, 4) as total_fees,
                    round(sum(net_pnl)::numeric, 4) as total_net,
                    round(avg(hold_seconds)::numeric, 0) as avg_hold
                FROM trade_outcomes WHERE mode IS NOT NULL
                GROUP BY mode ORDER BY total_net DESC
            """))
            return [dict(row) for row in r.mappings().all()]

    async def pnl_by_tag(self, user_id: int | None = None) -> list[dict]:
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT entry_tag, count(*) as trades,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    round(sum(case when net_pnl > 0 then 1.0 else 0 end) / count(*)::numeric * 100, 1) as wr,
                    round(sum(net_pnl)::numeric, 4) as total_pnl,
                    round(avg(net_pnl)::numeric, 4) as avg_pnl
                FROM trade_outcomes WHERE entry_tag IS NOT NULL
                GROUP BY entry_tag ORDER BY total_pnl DESC
            """))
            return [dict(row) for row in r.mappings().all()]

    async def pnl_by_exit_reason(self, user_id: int | None = None) -> list[dict]:
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT exit_reason, count(*) as trades,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    round(sum(net_pnl)::numeric, 4) as total_pnl,
                    round(avg(net_pnl)::numeric, 4) as avg_pnl,
                    round(avg(hold_seconds)::numeric, 0) as avg_hold
                FROM trade_outcomes WHERE exit_reason IS NOT NULL
                GROUP BY exit_reason ORDER BY trades DESC
            """))
            return [dict(row) for row in r.mappings().all()]

    async def fee_analysis(self, user_id: int | None = None) -> list[dict]:
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT coin, count(*) as trades,
                    round(sum(fee)::numeric, 4) as total_fees,
                    round(sum(gross_pnl)::numeric, 4) as total_gross,
                    round(sum(net_pnl)::numeric, 4) as total_net,
                    sum(case when gross_pnl > 0 and net_pnl <= 0 then 1 else 0 end) as fee_killed
                FROM trade_outcomes {uf_where} GROUP BY coin ORDER BY total_fees DESC
            """))
            return [dict(row) for row in r.mappings().all()]

    async def poison_coins(self, user_id: int | None = None) -> list[dict]:
        """Coins with worst performance — candidates for blocking."""
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT coin, side, count(*) as trades,
                    round(sum(net_pnl)::numeric, 4) as total_pnl,
                    round(avg(net_pnl)::numeric, 4) as avg_pnl,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    round(sum(case when net_pnl > 0 then 1.0 else 0 end) / greatest(count(*), 1)::numeric * 100, 1) as wr
                FROM trade_outcomes {uf_where} GROUP BY coin, side
                HAVING count(*) >= 3
                ORDER BY avg_pnl ASC LIMIT 15
            """))
            return [dict(row) for row in r.mappings().all()]

    async def rescuable_coins(self, user_id: int | None = None) -> list[dict]:
        """Coins with best performance — candidates for more allocation."""
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT coin, side, count(*) as trades,
                    round(sum(net_pnl)::numeric, 4) as total_pnl,
                    round(avg(net_pnl)::numeric, 4) as avg_pnl,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    round(sum(case when net_pnl > 0 then 1.0 else 0 end) / greatest(count(*), 1)::numeric * 100, 1) as wr
                FROM trade_outcomes {uf_where} GROUP BY coin, side
                HAVING count(*) >= 3
                ORDER BY avg_pnl DESC LIMIT 15
            """))
            return [dict(row) for row in r.mappings().all()]

    async def signal_blocked_vs_entered(self, user_id: int | None = None) -> list[dict]:
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT action, count(*) as total,
                    round(avg(signal_score)::numeric, 4) as avg_score,
                    round(avg(trend_score)::numeric, 4) as avg_trend,
                    round(avg(micro_score)::numeric, 4) as avg_micro
                FROM signal_evaluations
                GROUP BY action ORDER BY total DESC
            """))
            return [dict(row) for row in r.mappings().all()]

    async def daily_summary(self, user_id: int | None = None) -> list[dict]:
        uf_where = f"WHERE user_id = {user_id}" if user_id else ""
        async with self._sf() as s:
            r = await s.execute(text("""
                SELECT date(entry_time) as day,
                    count(*) as trades,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    round(sum(gross_pnl)::numeric, 4) as gross,
                    round(sum(fee)::numeric, 4) as fees,
                    round(sum(net_pnl)::numeric, 4) as net
                FROM trade_outcomes {uf_where} GROUP BY day ORDER BY day DESC
            """))
            return [dict(row) for row in r.mappings().all()]

    async def full_report(self, user_id: int | None = None) -> dict[str, Any]:
        """Run all reports and return as a single dict."""
        return {
            "wr_by_coin": await self.wr_by_coin(user_id),
            "wr_by_side": await self.wr_by_side(user_id),
            "wr_by_hour": await self.wr_by_hour(user_id),
            "pnl_by_mode": await self.pnl_by_mode(user_id),
            "pnl_by_tag": await self.pnl_by_tag(user_id),
            "pnl_by_exit_reason": await self.pnl_by_exit_reason(user_id),
            "fee_analysis": await self.fee_analysis(user_id),
            "poison_coins": await self.poison_coins(user_id),
            "rescuable_coins": await self.rescuable_coins(user_id),
            "signal_blocked_vs_entered": await self.signal_blocked_vs_entered(user_id),
            "daily_summary": await self.daily_summary(user_id),
        }
