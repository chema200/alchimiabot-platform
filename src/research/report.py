"""Research Reports: generate analysis from trade data.

Works both with PostgreSQL (live data) and DuckDB (parquet analysis).
"""

from typing import Any

import duckdb
import structlog

logger = structlog.get_logger()


class ResearchReport:
    """Generate research reports from trade history and parquet data."""

    def __init__(self, db_url: str | None = None, parquet_dir: str = "data/raw") -> None:
        self._db_url = db_url
        self._parquet_dir = parquet_dir

    def parquet_summary(self) -> dict[str, Any]:
        """Summarize what raw data we have in Parquet."""
        con = duckdb.connect()
        try:
            result = con.execute(f"""
                SELECT
                    count(*) as total_files,
                    sum(row_count) as total_rows
                FROM (
                    SELECT count(*) as row_count
                    FROM read_parquet('{self._parquet_dir}/**/*.parquet', union_by_name=true)
                    GROUP BY coin
                )
            """).fetchone()
            return {"files_scanned": True, "total_coins": result[0], "total_rows": result[1]}
        except Exception as e:
            return {"files_scanned": False, "error": str(e)}
        finally:
            con.close()

    def trade_cohorts_from_parquet(self, trades_parquet: str) -> dict[str, Any]:
        """Run cohort analysis over a trades parquet file (from replay output)."""
        con = duckdb.connect()
        try:
            # By coin
            by_coin = con.execute(f"""
                SELECT coin, count(*) as trades,
                       sum(case when net_pnl > 0 then 1 else 0 end) as wins,
                       round(avg(net_pnl), 4) as avg_pnl,
                       round(sum(net_pnl), 4) as total_pnl
                FROM read_parquet('{trades_parquet}')
                GROUP BY coin ORDER BY total_pnl DESC
            """).fetchdf().to_dict("records")

            # By exit reason
            by_reason = con.execute(f"""
                SELECT reason as exit_reason, count(*) as trades,
                       round(avg(net_pnl), 4) as avg_pnl,
                       round(sum(net_pnl), 4) as total_pnl
                FROM read_parquet('{trades_parquet}')
                GROUP BY reason ORDER BY trades DESC
            """).fetchdf().to_dict("records")

            return {"by_coin": by_coin, "by_exit_reason": by_reason}
        except Exception as e:
            return {"error": str(e)}
        finally:
            con.close()

    def feature_distribution(self, feature_name: str, coin: str | None = None) -> dict[str, Any]:
        """Analyze distribution of a feature from snapshots parquet."""
        con = duckdb.connect()
        try:
            where = f"WHERE coin = '{coin}'" if coin else ""
            stats = con.execute(f"""
                SELECT
                    count(*) as samples,
                    round(min(json_extract_string(features, '$.{feature_name}')::double), 6) as min_val,
                    round(avg(json_extract_string(features, '$.{feature_name}')::double), 6) as avg_val,
                    round(max(json_extract_string(features, '$.{feature_name}')::double), 6) as max_val,
                    round(stddev(json_extract_string(features, '$.{feature_name}')::double), 6) as std_val
                FROM read_parquet('{self._parquet_dir}/feature_snapshots/*.parquet')
                {where}
            """).fetchone()

            return {
                "feature": feature_name,
                "coin": coin,
                "samples": stats[0],
                "min": stats[1],
                "avg": stats[2],
                "max": stats[3],
                "std": stats[4],
            }
        except Exception as e:
            return {"feature": feature_name, "error": str(e)}
        finally:
            con.close()
