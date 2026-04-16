"""Change Markers Service: track changes and measure their impact."""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import json

import structlog
from sqlalchemy import text

logger = structlog.get_logger()


class MarkerService:
    """Manages change markers and impact calculation."""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def create_marker(self, **kwargs) -> int:
        """Create a new marker. Returns the new marker ID."""
        cfg = kwargs.get("config_snapshot")
        if cfg is not None and not isinstance(cfg, str):
            cfg = json.dumps(cfg)

        async with self._sf() as session:
            result = await session.execute(text("""
                INSERT INTO change_markers
                (user_id, timestamp, category, label, description, source, coin, side, mode, parameter,
                 old_value, new_value, batch_id, batch_label, config_snapshot)
                VALUES (:user_id, NOW(), :category, :label, :description, :source, :coin, :side, :mode, :parameter,
                        :old_value, :new_value, :batch_id, :batch_label, CAST(:config_snapshot AS JSONB))
                RETURNING id
            """), {
                "user_id": kwargs.get("user_id", 1),
                "category": kwargs.get("category", "MANUAL"),
                "label": kwargs.get("label", ""),
                "description": kwargs.get("description"),
                "source": kwargs.get("source", "USER"),
                "coin": kwargs.get("coin"),
                "side": kwargs.get("side"),
                "mode": kwargs.get("mode"),
                "parameter": kwargs.get("parameter"),
                "old_value": kwargs.get("old_value"),
                "new_value": kwargs.get("new_value"),
                "batch_id": kwargs.get("batch_id"),
                "batch_label": kwargs.get("batch_label"),
                "config_snapshot": cfg,
            })
            marker_id = result.scalar()
            await session.commit()
            logger.info(
                "marker.created",
                id=marker_id,
                category=kwargs.get("category"),
                label=kwargs.get("label"),
            )
            return marker_id

    async def get_markers(self, limit: int = 50, days: int = 90) -> list[dict]:
        """Get markers, optionally filtered by recent days."""
        async with self._sf() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            result = await session.execute(text("""
                SELECT * FROM change_markers
                WHERE timestamp >= :cutoff
                ORDER BY timestamp DESC
                LIMIT :limit
            """), {"cutoff": cutoff, "limit": limit})
            return [dict(r._mapping) for r in result]

    async def get_marker(self, marker_id: int) -> Optional[dict]:
        async with self._sf() as session:
            r = await session.execute(
                text("SELECT * FROM change_markers WHERE id = :id"),
                {"id": marker_id},
            )
            row = r.mappings().first()
            return dict(row) if row else None

    async def calculate_impact(self, marker_id: int, force: bool = False) -> dict:
        """Calculate before/after impact for a marker.

        Window: 20 trades before / 20 trades after.
        Fallback: 6 hours before / 6 hours after if not enough trades.
        Filter by context: if marker has coin/side/mode, only those trades.
        """
        marker = await self.get_marker(marker_id)
        if not marker:
            return {"error": "Marker not found"}

        async with self._sf() as session:
            # Build context filter
            filters = []
            params: dict[str, Any] = {"ts": marker["timestamp"]}
            if marker.get("coin"):
                filters.append("coin = :coin")
                params["coin"] = marker["coin"]
            if marker.get("side"):
                filters.append("side = :side")
                params["side"] = marker["side"]
            if marker.get("mode"):
                filters.append("mode = :mode")
                params["mode"] = marker["mode"]

            ctx = " AND " + " AND ".join(filters) if filters else ""

            # Try to get 20 trades before
            r_before = await session.execute(text(f"""
                SELECT * FROM trade_outcomes
                WHERE entry_time < :ts {ctx}
                ORDER BY entry_time DESC LIMIT 20
            """), params)
            before = [dict(r._mapping) for r in r_before]

            # Try to get 20 trades after
            r_after = await session.execute(text(f"""
                SELECT * FROM trade_outcomes
                WHERE entry_time >= :ts {ctx}
                ORDER BY entry_time ASC LIMIT 20
            """), params)
            after = [dict(r._mapping) for r in r_after]

            # Fallback to 6 hours if not enough
            if len(before) < 10 or len(after) < 10:
                t6h_before = marker["timestamp"] - timedelta(hours=6)
                t6h_after = marker["timestamp"] + timedelta(hours=6)
                params_h = dict(params)
                params_h["t_before"] = t6h_before
                params_h["t_after"] = t6h_after

                if len(before) < 10:
                    r = await session.execute(text(f"""
                        SELECT * FROM trade_outcomes
                        WHERE entry_time >= :t_before AND entry_time < :ts {ctx}
                        ORDER BY entry_time ASC
                    """), params_h)
                    before = [dict(row._mapping) for row in r]

                if len(after) < 10:
                    r = await session.execute(text(f"""
                        SELECT * FROM trade_outcomes
                        WHERE entry_time >= :ts AND entry_time <= :t_after {ctx}
                        ORDER BY entry_time ASC
                    """), params_h)
                    after = [dict(row._mapping) for row in r]

            # Calculate metrics
            def metrics(trades: list[dict]) -> dict:
                if not trades:
                    return {
                        "trades": 0, "wins": 0, "losses": 0,
                        "wr": 0, "expectancy": 0, "pf": 0,
                        "sl_rate": 0, "total_pnl": 0, "avg_pnl": 0,
                    }
                wins = sum(1 for t in trades if (t.get("net_pnl") or 0) > 0)
                losses = sum(1 for t in trades if (t.get("net_pnl") or 0) < 0)
                total_pnl = sum((t.get("net_pnl") or 0) for t in trades)
                gross_wins = sum((t.get("net_pnl") or 0) for t in trades if (t.get("net_pnl") or 0) > 0)
                gross_losses = abs(sum((t.get("net_pnl") or 0) for t in trades if (t.get("net_pnl") or 0) < 0))
                sl_count = sum(1 for t in trades if t.get("exit_reason") == "SL")
                return {
                    "trades": len(trades),
                    "wins": wins,
                    "losses": losses,
                    "wr": round(wins / len(trades) * 100, 1),
                    "expectancy": round(total_pnl / len(trades), 4),
                    "pf": round(gross_wins / gross_losses, 2) if gross_losses > 0 else 0,
                    "sl_rate": round(sl_count / len(trades) * 100, 1),
                    "total_pnl": round(total_pnl, 4),
                    "avg_pnl": round(total_pnl / len(trades), 4),
                }

            m_before = metrics(before)
            m_after = metrics(after)

            # Determine status
            if len(before) < 10 or len(after) < 10:
                status = "INSUFFICIENT_DATA"
            else:
                delta_exp = m_after["expectancy"] - m_before["expectancy"]
                delta_wr = m_after["wr"] - m_before["wr"]
                delta_pf = m_after["pf"] - m_before["pf"]
                delta_sl_rate = m_after["sl_rate"] - m_before["sl_rate"]

                # Simple weighted score
                score = (
                    (delta_exp * 0.40)
                    + (delta_wr / 100 * 0.30)
                    + (delta_pf * 0.20)
                    + (-delta_sl_rate / 100 * 0.10)
                )

                if score > 0.10:
                    status = "IMPROVED"
                elif score < -0.10:
                    status = "WORSENED"
                else:
                    status = "NEUTRAL"

            impact_data = {
                "before": m_before,
                "after": m_after,
                "delta_expectancy": round(m_after["expectancy"] - m_before["expectancy"], 4),
                "delta_wr": round(m_after["wr"] - m_before["wr"], 1),
                "delta_pf": round(m_after["pf"] - m_before["pf"], 2),
                "delta_sl_rate": round(m_after["sl_rate"] - m_before["sl_rate"], 1),
            }

            await session.execute(text("""
                UPDATE change_markers
                SET impact_status = :status,
                    impact_data = CAST(:data AS JSONB),
                    impact_calculated_at = NOW()
                WHERE id = :id
            """), {"status": status, "data": json.dumps(impact_data), "id": marker_id})
            await session.commit()

            return {"status": status, "impact": impact_data}

    async def get_recent_with_impact(self, limit: int = 10) -> list[dict]:
        """Get recent markers with their impact pre-calculated (or calculate on-demand)."""
        markers = await self.get_markers(limit=limit, days=30)
        result = []
        for m in markers:
            # If no impact yet and marker is older than 30min, try to calculate
            if m.get("impact_status") == "PENDING":
                ts = m["timestamp"]
                if ts is not None:
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    if age > 1800:  # 30 min
                        impact = await self.calculate_impact(m["id"])
                        m["impact_status"] = impact.get("status")
                        m["impact_data"] = impact.get("impact")
            result.append(m)
        return result
