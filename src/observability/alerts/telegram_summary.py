"""Telegram Summary: sends periodic platform status reports in Spanish.

Runs every 4 hours, analyzes recent data, and sends actionable insights
to Telegram. Uses the same bot token as agentbot-live.
"""

import asyncio
import os
import httpx
import structlog
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import text

logger = structlog.get_logger()

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Bot DB connection string to read user_notification_settings
BOT_DB_URL = os.environ.get(
    "BOT_DB_URL",
    f"postgresql://{os.environ.get('BOT_DB_USER', 'alchimiabot')}:{os.environ.get('BOT_DB_PASSWORD', 'alchimiabot')}@{os.environ.get('BOT_DB_HOST', 'localhost')}:{os.environ.get('BOT_DB_PORT', '5442')}/{os.environ.get('BOT_DB_NAME', 'alchimiabot')}"
)


class TelegramSummaryService:
    """Sends periodic platform analysis summaries via Telegram.

    Reads telegram credentials PER USER from the bot's user_notification_settings table.
    Sends the summary to all users with telegram_enabled=true.
    """

    def __init__(self, session_factory, interval_hours: int = 4) -> None:
        self._sf = session_factory
        self._interval = interval_hours * 3600
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("telegram_summary.started", interval_h=self._interval // 3600)
        # Wait 2 min after startup before first report
        await asyncio.sleep(120)
        while self._running:
            try:
                await self._send_summary()
            except Exception:
                logger.exception("telegram_summary.error")
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        self._running = False

    async def _send_summary(self) -> None:
        data = await self._gather_data()
        message = self._build_message(data)
        await self._send_telegram(message)

    async def _gather_data(self) -> dict[str, Any]:
        """Gather all metrics for the summary."""
        result = {}
        hours = self._interval // 3600

        async with self._sf() as s:
            # Trades in period
            r = await s.execute(text(f"""
                SELECT count(*) as trades,
                    sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                    sum(case when net_pnl <= 0 then 1 else 0 end) as losses,
                    round(sum(gross_pnl)::numeric, 4) as gross,
                    round(sum(fee)::numeric, 4) as fees,
                    round(sum(net_pnl)::numeric, 4) as net,
                    round(avg(net_pnl)::numeric, 4) as avg_pnl
                FROM trade_outcomes
                WHERE exit_time > now() - interval '{hours} hours'
            """))
            row = r.mappings().first()
            result["trades"] = dict(row) if row else {}

            # By quality label
            r = await s.execute(text(f"""
                SELECT entry_quality_label as label, count(*) as cnt,
                    round(avg(net_pnl)::numeric, 4) as avg_pnl
                FROM trade_outcomes
                WHERE exit_time > now() - interval '{hours} hours' AND entry_quality_label IS NOT NULL
                GROUP BY entry_quality_label ORDER BY avg_pnl DESC
            """))
            result["by_quality"] = [dict(row) for row in r.mappings().all()]

            # By late entry risk
            r = await s.execute(text(f"""
                SELECT late_entry_risk as risk, count(*) as cnt,
                    round(avg(net_pnl)::numeric, 4) as avg_pnl
                FROM trade_outcomes
                WHERE exit_time > now() - interval '{hours} hours' AND late_entry_risk IS NOT NULL
                GROUP BY late_entry_risk ORDER BY avg_pnl DESC
            """))
            result["by_late_risk"] = [dict(row) for row in r.mappings().all()]

            # Signal rejection breakdown
            r = await s.execute(text(f"""
                SELECT reason, count(*) as cnt
                FROM signal_evaluations
                WHERE timestamp > now() - interval '{hours} hours' AND action = 'BLOCKED'
                GROUP BY reason ORDER BY cnt DESC LIMIT 5
            """))
            result["top_rejections"] = [dict(row) for row in r.mappings().all()]

            # Worst coins
            r = await s.execute(text(f"""
                SELECT coin, count(*) as trades, round(sum(net_pnl)::numeric, 4) as pnl
                FROM trade_outcomes
                WHERE exit_time > now() - interval '{hours} hours'
                GROUP BY coin HAVING count(*) >= 2
                ORDER BY pnl ASC LIMIT 3
            """))
            result["worst_coins"] = [dict(row) for row in r.mappings().all()]

            # Best coins
            r = await s.execute(text(f"""
                SELECT coin, count(*) as trades, round(sum(net_pnl)::numeric, 4) as pnl
                FROM trade_outcomes
                WHERE exit_time > now() - interval '{hours} hours'
                GROUP BY coin HAVING count(*) >= 2
                ORDER BY pnl DESC LIMIT 3
            """))
            result["best_coins"] = [dict(row) for row in r.mappings().all()]

            # Score parity
            r = await s.execute(text("""
                SELECT count(*) as total,
                    count(case when signal_score > 0 then 1 end) as has_score
                FROM trade_outcomes WHERE entry_quality_label IS NOT NULL
            """))
            sp = r.mappings().first()
            result["score_coverage"] = round(float(sp["has_score"]) / max(float(sp["total"]), 1) * 100, 1) if sp else 0

            # Total signals
            r = await s.execute(text(f"""
                SELECT count(*) as total,
                    sum(case when action = 'ENTER' then 1 else 0 end) as enters,
                    sum(case when action = 'BLOCKED' then 1 else 0 end) as blocked
                FROM signal_evaluations
                WHERE timestamp > now() - interval '{hours} hours'
            """))
            result["signals"] = dict(r.mappings().first()) if r else {}

        return result

    def _build_message(self, data: dict) -> str:
        hours = self._interval // 3600
        t = data.get("trades", {})
        trades = int(t.get("trades") or 0)
        wins = int(t.get("wins") or 0)
        losses = int(t.get("losses") or 0)
        net = float(t.get("net") or 0)
        fees = float(t.get("fees") or 0)
        gross = float(t.get("gross") or 0)

        sig = data.get("signals", {})
        enters = int(sig.get("enters") or 0)
        blocked = int(sig.get("blocked") or 0)

        wr = round(wins / trades * 100) if trades > 0 else 0

        lines = []
        lines.append(f"📊 *RESUMEN PLATFORM ({hours}h)*")
        lines.append(f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M')} UTC_")
        lines.append("")

        # PnL
        emoji = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
        lines.append(f"{emoji} *PnL: ${net:.4f}* (bruto: ${gross:.4f}, fees: ${fees:.4f})")
        lines.append(f"📈 Trades: {trades} ({wins}W/{losses}L) — WR: {wr}%")

        # Signals
        if enters + blocked > 0:
            lines.append(f"📡 Senales: {enters} entradas, {blocked} bloqueadas")

        # Quality
        by_q = data.get("by_quality", [])
        if by_q:
            lines.append("")
            lines.append("*Calidad de entrada:*")
            for q in by_q:
                emoji_q = "🅰️" if "A" in str(q.get("label", "")) else "🅱️" if q.get("label") == "B" else "⬜"
                lines.append(f"  {emoji_q} {q['label']}: {q['cnt']} trades, avg ${q['avg_pnl']}")

        # Late risk
        by_lr = data.get("by_late_risk", [])
        if by_lr:
            lines.append("")
            lines.append("*Riesgo entrada tardia:*")
            for lr in by_lr:
                emoji_r = "🟢" if lr["risk"] == "LOW" else "🟡" if lr["risk"] == "MEDIUM" else "🔴"
                lines.append(f"  {emoji_r} {lr['risk']}: {lr['cnt']} trades, avg ${lr['avg_pnl']}")

        # Top rejections
        rejs = data.get("top_rejections", [])
        if rejs:
            lines.append("")
            lines.append("*Top bloqueos:*")
            for r in rejs[:3]:
                lines.append(f"  🚫 {r['reason']}: {r['cnt']}")

        # Best/Worst coins
        best = data.get("best_coins", [])
        worst = data.get("worst_coins", [])
        if best or worst:
            lines.append("")
            if best:
                coins_str = ", ".join(f"{c['coin']}(${c['pnl']})" for c in best)
                lines.append(f"🏆 Mejores: {coins_str}")
            if worst:
                coins_str = ", ".join(f"{c['coin']}(${c['pnl']})" for c in worst)
                lines.append(f"💀 Peores: {coins_str}")

        # Recommendations
        lines.append("")
        lines.append("*Sugerencias:*")

        if trades == 0:
            lines.append("  ⚠️ Sin trades — mercado plano o filtros demasiado restrictivos")
        elif wr < 35:
            lines.append("  ⚠️ WR bajo — revisar calidad de entradas")
        elif wr >= 50:
            lines.append("  ✅ WR positivo — sistema funcionando bien")

        if any(q.get("label") == "B" and float(q.get("avg_pnl", 0)) < -0.3 for q in by_q):
            lines.append("  🔧 Trades B siguen perdiendo — considerar subir minScoreExcess")

        if any(lr.get("risk") == "HIGH" and float(lr.get("avg_pnl", 0)) < -0.3 for lr in by_lr):
            lines.append("  🔧 Late entry HIGH pierde — filtro de timing podria ser mas estricto")

        fee_pct = abs(fees / gross * 100) if gross != 0 else 0
        if fee_pct > 30 and trades > 3:
            lines.append(f"  💰 Fees = {fee_pct:.0f}% del bruto — trades demasiado pequenos")

        coverage = data.get("score_coverage", 0)
        if coverage < 90:
            lines.append(f"  📉 Score coverage: {coverage}% — datos incompletos")

        return "\n".join(lines)

    async def _send_telegram(self, message: str) -> None:
        """Send summary to all users with telegram enabled (reads from bot DB)."""
        recipients = await self._get_telegram_recipients()
        if not recipients:
            logger.info("telegram_summary.no_recipients")
            return

        async with httpx.AsyncClient(timeout=10) as client:
            for user_id, bot_token, chat_id in recipients:
                try:
                    url = TELEGRAM_API.format(token=bot_token)
                    r = await client.post(url, json={
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    })
                    if r.status_code == 200:
                        logger.info("telegram_summary.sent", user_id=user_id, length=len(message))
                    else:
                        logger.warning("telegram_summary.send_failed", user_id=user_id, status=r.status_code, body=r.text[:200])
                except Exception as e:
                    logger.warning("telegram_summary.send_error", user_id=user_id, error=str(e))

    async def _get_telegram_recipients(self) -> list[tuple[int, str, str]]:
        """Read all users with telegram_enabled from the bot's DB."""
        import asyncpg
        try:
            conn = await asyncpg.connect(BOT_DB_URL)
            rows = await conn.fetch(
                "SELECT user_id, telegram_bot_token, telegram_chat_id "
                "FROM user_notification_settings "
                "WHERE telegram_enabled = true AND telegram_bot_token IS NOT NULL AND telegram_chat_id IS NOT NULL"
            )
            await conn.close()
            return [(r["user_id"], r["telegram_bot_token"], r["telegram_chat_id"]) for r in rows]
        except Exception as e:
            logger.warning("telegram_summary.recipients_error", error=str(e))
            return []
