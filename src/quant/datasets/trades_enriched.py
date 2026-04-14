"""Trades Enriched Dataset: unifies trades + signals + features into one analytical structure.

This is THE core dataset for all quant analysis. Every trade gets enriched with:
- features at entry time (temporal JOIN)
- signal scores and filter decisions
- market context (regime, BTC trend)
- execution quality metrics
"""

from datetime import datetime, date, timezone
from typing import Any

import structlog
from sqlalchemy import text

logger = structlog.get_logger()


def _coerce_date(value: Any) -> Any:
    """Convert string date/datetime to a Python date/datetime for asyncpg.

    Accepts: None, datetime, date, ISO-8601 string ('2026-04-10' or
    '2026-04-10T12:00:00' or with timezone). Returns the input unchanged
    if it's already a date/datetime, or None if input is None/empty.
    asyncpg requires real date/datetime objects, not strings.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError:
                logger.warning("trades_enriched.invalid_date", value=value)
                return None
    return value


class TradesEnrichedBuilder:
    """Builds the enriched trades dataset from platform DB."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def build(self, date_from: str | None = None, date_to: str | None = None,
                    coins: list[str] | None = None) -> list[dict[str, Any]]:
        """Build enriched trades dataset.

        Each row = one completed trade with full context at entry.
        """
        filters = []
        params: dict[str, Any] = {}

        df = _coerce_date(date_from)
        dt = _coerce_date(date_to)
        if df is not None:
            filters.append("t.entry_time >= :date_from")
            params["date_from"] = df
        if dt is not None:
            filters.append("t.entry_time < :date_to")
            params["date_to"] = dt
        if coins:
            filters.append("t.coin = ANY(:coins)")
            params["coins"] = coins

        where = "WHERE " + " AND ".join(filters) if filters else ""

        async with self._sf() as s:
            # Get trades with their closest signal evaluation and feature snapshot
            result = await s.execute(text(f"""
                SELECT
                    t.id as trade_id,
                    t.entry_time as timestamp,
                    t.coin,
                    t.side,

                    -- PnL
                    t.net_pnl as pnl,
                    CASE WHEN t.notional > 0 THEN t.net_pnl / t.notional * 100 ELSE 0 END as pnl_pct,
                    t.hold_seconds as duration_seconds,
                    t.entry_price,
                    t.exit_price,
                    t.gross_pnl,
                    t.fee,

                    -- Exit
                    t.exit_reason as exit_type,
                    t.exit_time,

                    -- Scores
                    t.signal_score as score_total,
                    t.trend_score,
                    t.micro_score,

                    -- Performance
                    t.mfe_pct,
                    t.mae_pct,
                    t.high_water_mark,

                    -- Config tracking
                    t.score_min_applied,
                    t.config_version,
                    t.momentum_score,

                    -- Entry quality diagnostics
                    t.entry_quality_label,
                    t.late_entry_risk,

                    -- Context
                    t.mode,
                    t.entry_tag,
                    t.regime,
                    t.leverage,
                    t.notional,

                    -- Config snapshot
                    t.config_snapshot,

                    -- Features at entry (from closest snapshot)
                    fs.features as features_at_entry,
                    fs.version as feature_version,

                    -- Linked signal evaluation (decision_trace, features at signal time)
                    se.id as signal_eval_id,
                    se.decision_trace as decision_trace,
                    se.diagnostic_trace as diagnostic_trace,
                    se.features as signal_features,
                    se.entry_diagnostics as entry_diagnostics,
                    se.reason as signal_reason

                FROM trade_outcomes t
                LEFT JOIN LATERAL (
                    SELECT features, version
                    FROM feature_snapshots
                    WHERE coin = t.coin
                      AND timestamp <= t.entry_time
                    ORDER BY timestamp DESC
                    LIMIT 1
                ) fs ON true
                LEFT JOIN signal_evaluations se ON se.trade_outcome_id = t.id
                {where}
                ORDER BY t.entry_time DESC
            """), params)

            rows = result.mappings().all()

        # Enrich each trade
        enriched = []
        for row in rows:
            trade = dict(row)

            # Extract key features from snapshot
            features = trade.pop("features_at_entry", None) or {}
            trade["features"] = {
                "rsi": features.get("trend_ema_cross", 0),  # proxy
                "atr": features.get("vol_atr_5m", 0),
                "volatility": features.get("vol_realized_5m", 0),
                "spread": features.get("micro_spread_bps", 0),
                "imbalance": features.get("micro_imbalance", 0),
                "volume_delta": features.get("micro_trade_imbalance", 0),
                "trade_speed": features.get("micro_intensity", 0),
                "buy_pressure": features.get("mom_buy_pressure", 0),
                "trend_strength": features.get("trend_strength", 0),
                "trend_r2": features.get("trend_r2_5m", 0),
                "trend_consistency": features.get("trend_consistency", 0),
                "mom_ret_2m": features.get("mom_ret_2m", 0),
                "mom_ret_5m": features.get("mom_ret_5m", 0),
                "vol_ratio": features.get("vol_ratio_1m_5m", 0),
                "vpin": features.get("micro_vpin", 0),
                "hour_sin": features.get("temp_hour_sin", 0),
                "hour_cos": features.get("temp_hour_cos", 0),
                "is_weekend": features.get("temp_is_weekend", 0),
                "session": features.get("temp_session", 0),
            }

            # Extract key config params from config_snapshot for easy access
            cs = trade.get("config_snapshot") or {}
            trade["cfg_stop_loss_pct"] = cs.get("stop_loss_pct")
            trade["cfg_take_profit_pct"] = cs.get("take_profit_pct")
            trade["cfg_trailing_distance_pct"] = cs.get("trailing_distance_pct")
            trade["cfg_partial_close_pct"] = cs.get("partial_close_pct")
            trade["cfg_min_score_total"] = cs.get("min_score_total")
            trade["cfg_trailing_mode"] = cs.get("trailing_mode")
            trade["cfg_atr_sl_enabled"] = cs.get("atr_sl_enabled")
            trade["cfg_atr_sl_multiplier"] = cs.get("atr_sl_multiplier")

            # Derived labels
            trade["outcome"] = "WIN" if (trade.get("pnl") or 0) > 0 else "LOSS"
            trade["fee_killed"] = (trade.get("gross_pnl") or 0) > 0 and (trade.get("pnl") or 0) <= 0
            pnl_pct = trade.get("pnl_pct", 0)
            if pnl_pct > 0.1:
                trade["expectancy_bucket"] = "good"
            elif pnl_pct >= 0:
                trade["expectancy_bucket"] = "marginal"
            else:
                trade["expectancy_bucket"] = "bad"

            hold = trade.get("duration_seconds") or 0
            if hold < 120:
                trade["hold_bucket"] = "fast"
            elif hold < 600:
                trade["hold_bucket"] = "normal"
            else:
                trade["hold_bucket"] = "slow"

            # Decision trace (from linked signal_evaluation, may be None for pre-link trades)
            dt = trade.get("decision_trace") or {}
            trade["change_signal"] = dt.get("changeSignal")
            trade["change_confirm"] = dt.get("changeConfirm")
            trade["entry_threshold"] = dt.get("entryThreshold")
            cs_v = trade.get("change_signal")
            cc_v = trade.get("change_confirm")
            if cs_v is not None and cc_v is not None and cs_v != 0:
                trade["confirm_signal_ratio"] = abs(cc_v) / abs(cs_v)
            else:
                trade["confirm_signal_ratio"] = None

            # Score bucket
            score = trade.get("score_total") or 0
            if score >= 80:
                trade["score_bucket"] = "80+"
            elif score >= 60:
                trade["score_bucket"] = "60-80"
            elif score >= 40:
                trade["score_bucket"] = "40-60"
            else:
                trade["score_bucket"] = "0-40"

            enriched.append(trade)

        logger.info("trades_enriched.built", count=len(enriched))
        return enriched

    async def build_with_signals(self, date_from: str | None = None) -> dict[str, Any]:
        """Build enriched dataset including blocked/skipped signals."""
        trades = await self.build(date_from=date_from)

        # Get signal evaluations
        params: dict[str, Any] = {}
        where = ""
        df = _coerce_date(date_from)
        if df is not None:
            where = "WHERE timestamp >= :date_from"
            params["date_from"] = df

        async with self._sf() as s:
            result = await s.execute(text(f"""
                SELECT action, count(*) as cnt,
                       round(avg(signal_score)::numeric, 2) as avg_score,
                       string_agg(DISTINCT reason, ', ') as reasons
                FROM signal_evaluations
                {where}
                GROUP BY action
            """), params)
            signal_summary = {row["action"]: dict(row) for row in result.mappings().all()}

        return {
            "trades": trades,
            "total_trades": len(trades),
            "signals": signal_summary,
        }
