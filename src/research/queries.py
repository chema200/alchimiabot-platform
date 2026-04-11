"""Research Queries: SQL-based analysis over trade outcomes and signal evaluations.

These are the building blocks for notebooks and reports.
All queries run against the platform PostgreSQL or read from DuckDB over Parquet.
"""

from typing import Any

import structlog

logger = structlog.get_logger()


# ── Queries for PostgreSQL (via SQLAlchemy) ──────────────────────────────

COHORT_BY_COIN = """
SELECT coin, side, count(*) as trades,
       sum(case when net_pnl > 0 then 1 else 0 end) as wins,
       round(avg(net_pnl)::numeric, 4) as avg_pnl,
       round(sum(net_pnl)::numeric, 4) as total_pnl,
       round(avg(hold_seconds)::numeric, 0) as avg_hold_sec,
       round(avg(mfe_pct)::numeric, 4) as avg_mfe,
       round(avg(mae_pct)::numeric, 4) as avg_mae
FROM trade_outcomes
GROUP BY coin, side
ORDER BY total_pnl DESC
"""

COHORT_BY_HOUR = """
SELECT extract(hour from entry_time) as hour,
       count(*) as trades,
       sum(case when net_pnl > 0 then 1 else 0 end) as wins,
       round(avg(net_pnl)::numeric, 4) as avg_pnl,
       round(sum(net_pnl)::numeric, 4) as total_pnl
FROM trade_outcomes
GROUP BY hour
ORDER BY hour
"""

COHORT_BY_REGIME = """
SELECT regime, count(*) as trades,
       sum(case when net_pnl > 0 then 1 else 0 end) as wins,
       round(avg(net_pnl)::numeric, 4) as avg_pnl,
       round(sum(net_pnl)::numeric, 4) as total_pnl,
       round(avg(hold_seconds)::numeric, 0) as avg_hold_sec
FROM trade_outcomes
WHERE regime IS NOT NULL
GROUP BY regime
ORDER BY total_pnl DESC
"""

COHORT_BY_MODE = """
SELECT mode, count(*) as trades,
       sum(case when net_pnl > 0 then 1 else 0 end) as wins,
       round(avg(net_pnl)::numeric, 4) as avg_pnl,
       round(sum(net_pnl)::numeric, 4) as total_pnl
FROM trade_outcomes
WHERE mode IS NOT NULL
GROUP BY mode
ORDER BY total_pnl DESC
"""

COHORT_BY_EXIT_REASON = """
SELECT exit_reason, count(*) as trades,
       sum(case when net_pnl > 0 then 1 else 0 end) as wins,
       round(avg(net_pnl)::numeric, 4) as avg_pnl,
       round(sum(net_pnl)::numeric, 4) as total_pnl,
       round(avg(hold_seconds)::numeric, 0) as avg_hold_sec
FROM trade_outcomes
WHERE exit_reason IS NOT NULL
GROUP BY exit_reason
ORDER BY trades DESC
"""

SIGNAL_HIT_RATE = """
SELECT action, count(*) as total,
       round(avg(signal_score)::numeric, 4) as avg_score,
       round(avg(trend_score)::numeric, 4) as avg_trend,
       round(avg(micro_score)::numeric, 4) as avg_micro
FROM signal_evaluations
GROUP BY action
ORDER BY total DESC
"""

SIGNAL_SCORE_BUCKETS = """
SELECT
  case
    when signal_score >= 0.8 then '0.8+'
    when signal_score >= 0.6 then '0.6-0.8'
    when signal_score >= 0.4 then '0.4-0.6'
    when signal_score >= 0.2 then '0.2-0.4'
    else '0-0.2'
  end as score_bucket,
  count(*) as total,
  sum(case when action = 'ENTER' then 1 else 0 end) as entered,
  sum(case when action = 'SKIP' then 1 else 0 end) as skipped,
  sum(case when action = 'BLOCKED' then 1 else 0 end) as blocked
FROM signal_evaluations
GROUP BY score_bucket
ORDER BY score_bucket DESC
"""

DAILY_PNL = """
SELECT date(entry_time) as trade_date,
       count(*) as trades,
       sum(case when net_pnl > 0 then 1 else 0 end) as wins,
       round(sum(gross_pnl)::numeric, 4) as gross_pnl,
       round(sum(fee)::numeric, 4) as total_fees,
       round(sum(net_pnl)::numeric, 4) as net_pnl
FROM trade_outcomes
GROUP BY trade_date
ORDER BY trade_date DESC
"""

WORST_TRADES = """
SELECT coin, side, entry_time, exit_reason, regime, mode,
       round(net_pnl::numeric, 4) as net_pnl,
       round(gross_pnl::numeric, 4) as gross_pnl,
       round(fee::numeric, 4) as fee,
       hold_seconds, entry_tag,
       round(signal_score::numeric, 4) as signal_score
FROM trade_outcomes
ORDER BY net_pnl ASC
LIMIT 20
"""

BEST_TRADES = """
SELECT coin, side, entry_time, exit_reason, regime, mode,
       round(net_pnl::numeric, 4) as net_pnl,
       round(gross_pnl::numeric, 4) as gross_pnl,
       round(fee::numeric, 4) as fee,
       hold_seconds, entry_tag,
       round(signal_score::numeric, 4) as signal_score
FROM trade_outcomes
ORDER BY net_pnl DESC
LIMIT 20
"""

FEE_ANALYSIS = """
SELECT coin,
       count(*) as trades,
       round(sum(fee)::numeric, 4) as total_fees,
       round(sum(gross_pnl)::numeric, 4) as total_gross,
       round(sum(net_pnl)::numeric, 4) as total_net,
       round((sum(fee) / nullif(sum(abs(gross_pnl)), 0) * 100)::numeric, 2) as fee_pct_of_gross
FROM trade_outcomes
GROUP BY coin
ORDER BY total_fees DESC
"""

# ── All queries as a dict for easy access ──

QUERIES = {
    "cohort_by_coin": COHORT_BY_COIN,
    "cohort_by_hour": COHORT_BY_HOUR,
    "cohort_by_regime": COHORT_BY_REGIME,
    "cohort_by_mode": COHORT_BY_MODE,
    "cohort_by_exit_reason": COHORT_BY_EXIT_REASON,
    "signal_hit_rate": SIGNAL_HIT_RATE,
    "signal_score_buckets": SIGNAL_SCORE_BUCKETS,
    "daily_pnl": DAILY_PNL,
    "worst_trades": WORST_TRADES,
    "best_trades": BEST_TRADES,
    "fee_analysis": FEE_ANALYSIS,
}
