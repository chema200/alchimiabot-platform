"""Replay Builder: generates simulated trades from historical parquet data.

Uses captured raw trades/book data to simulate what the bot WOULD have done
with different configurations. Not a full replay engine — uses simplified
signal detection based on momentum and features.
"""

import os
import glob
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import structlog

logger = structlog.get_logger()


class ReplayBuilder:
    """Builds simulated trades from historical parquet data."""

    def __init__(self, data_dir: str = "data/raw") -> None:
        self._data_dir = data_dir

    def build(self, date_from: str | None = None, date_to: str | None = None,
              coins: list[str] | None = None) -> list[dict]:
        """Load historical data and simulate trades.

        Returns enriched trade dicts compatible with MetricsEngine/AnalysisEngine.
        """
        # Load raw trades from parquet
        raw_trades = self._load_trades(date_from, date_to, coins)
        if not raw_trades:
            logger.warning("replay_builder.no_data", date_from=date_from, date_to=date_to)
            return []

        # Group by coin
        by_coin: dict[str, list[dict]] = {}
        for t in raw_trades:
            coin = t.get("coin", "")
            if coin:
                by_coin.setdefault(coin, []).append(t)

        # Simulate trades per coin
        simulated = []
        for coin, trades in by_coin.items():
            trades.sort(key=lambda t: t.get("timestamp_ms", 0))
            sim = self._simulate_coin(coin, trades)
            simulated.extend(sim)

        simulated.sort(key=lambda t: t.get("timestamp", datetime.min), reverse=True)
        logger.info("replay_builder.built", trades=len(simulated), coins=len(by_coin),
                     raw_events=len(raw_trades))
        return simulated

    def _load_trades(self, date_from: str | None, date_to: str | None,
                     coins: list[str] | None) -> list[dict]:
        """Load trade events from parquet files."""
        trade_dir = os.path.join(self._data_dir, "trade")
        if not os.path.exists(trade_dir):
            return []

        all_rows = []
        for coin_dir in sorted(os.listdir(trade_dir)):
            if coins and coin_dir not in coins:
                continue

            coin_path = os.path.join(trade_dir, coin_dir)
            if not os.path.isdir(coin_path):
                continue

            for pq_file in sorted(glob.glob(os.path.join(coin_path, "**/*.parquet"), recursive=True)):
                # Date filter from path
                parts = pq_file.replace(coin_path, "").strip(os.sep).split(os.sep)
                file_date = parts[0] if parts else ""
                if date_from and file_date < date_from:
                    continue
                if date_to and file_date > date_to:
                    continue

                try:
                    table = pq.read_table(pq_file)
                    rows = table.to_pylist()
                    for row in rows:
                        row["coin"] = coin_dir
                    all_rows.extend(rows)
                except Exception:
                    pass

        return all_rows

    def _simulate_coin(self, coin: str, trades: list[dict],
                       entry_threshold_pct: float = 0.3,
                       sl_pct: float = 0.4, tp_pct: float = 0.6,
                       min_interval_ms: int = 300000) -> list[dict]:
        """Simulate directional trades for one coin using momentum signals.

        Simple strategy: enter when 2-min momentum exceeds threshold,
        exit at SL/TP or timeout (15 min).
        """
        simulated = []
        if len(trades) < 50:
            return simulated

        prices = [(t.get("timestamp_ms", 0), t.get("price", 0)) for t in trades if t.get("price", 0) > 0]
        if len(prices) < 50:
            return simulated

        in_position = False
        entry_price = 0
        entry_time = 0
        entry_side = ""
        hwm = 0
        last_entry_time = 0

        for i in range(30, len(prices)):
            ts, px = prices[i]
            if px <= 0:
                continue

            if in_position:
                # Update HWM
                if entry_side == "LONG" and px > hwm:
                    hwm = px
                elif entry_side == "SHORT" and px < hwm:
                    hwm = px

                # Check SL
                if entry_side == "LONG" and px <= entry_price * (1 - sl_pct / 100):
                    sim = self._close_trade(coin, entry_side, entry_price, px, entry_time, ts, "SL", hwm)
                    simulated.append(sim)
                    in_position = False
                elif entry_side == "SHORT" and px >= entry_price * (1 + sl_pct / 100):
                    sim = self._close_trade(coin, entry_side, entry_price, px, entry_time, ts, "SL", hwm)
                    simulated.append(sim)
                    in_position = False
                # Check TP
                elif entry_side == "LONG" and px >= entry_price * (1 + tp_pct / 100):
                    sim = self._close_trade(coin, entry_side, entry_price, px, entry_time, ts, "TP", hwm)
                    simulated.append(sim)
                    in_position = False
                elif entry_side == "SHORT" and px <= entry_price * (1 - tp_pct / 100):
                    sim = self._close_trade(coin, entry_side, entry_price, px, entry_time, ts, "TP", hwm)
                    simulated.append(sim)
                    in_position = False
                # Timeout (15 min)
                elif ts - entry_time > 900000:
                    sim = self._close_trade(coin, entry_side, entry_price, px, entry_time, ts, "TIMEOUT", hwm)
                    simulated.append(sim)
                    in_position = False
            else:
                # Cooldown
                if ts - last_entry_time < min_interval_ms:
                    continue

                # Momentum signal: 2-min lookback
                lookback_ts = ts - 120000
                lookback_prices = [p for t_ms, p in prices[max(0, i - 60):i] if t_ms >= lookback_ts]
                if len(lookback_prices) < 3:
                    continue

                ret_2m = (px - lookback_prices[0]) / lookback_prices[0] * 100

                if ret_2m >= entry_threshold_pct:
                    in_position = True
                    entry_price = px
                    entry_time = ts
                    entry_side = "LONG"
                    hwm = px
                    last_entry_time = ts
                elif ret_2m <= -entry_threshold_pct:
                    in_position = True
                    entry_price = px
                    entry_time = ts
                    entry_side = "SHORT"
                    hwm = px
                    last_entry_time = ts

        return simulated

    def _close_trade(self, coin: str, side: str, entry_px: float, exit_px: float,
                     entry_ts: int, exit_ts: int, reason: str, hwm: float) -> dict:
        """Create a simulated trade result."""
        fee_rate = 0.00045  # taker
        notional = 300  # $100 margin * 3x leverage
        size = notional / entry_px

        if side == "LONG":
            gross = (exit_px - entry_px) * size
            mfe = (hwm - entry_px) / entry_px * 100
            mae = min(0, (exit_px - entry_px) / entry_px * 100) if reason == "SL" else 0
        else:
            gross = (entry_px - exit_px) * size
            mfe = (entry_px - hwm) / entry_px * 100
            mae = min(0, (entry_px - exit_px) / entry_px * 100) if reason == "SL" else 0

        fee = (entry_px * size + exit_px * size) * fee_rate
        net = gross - fee
        hold = (exit_ts - entry_ts) / 1000

        entry_dt = datetime.fromtimestamp(entry_ts / 1000, tz=timezone.utc)
        exit_dt = datetime.fromtimestamp(exit_ts / 1000, tz=timezone.utc)

        return {
            "trade_id": None,
            "timestamp": entry_dt,
            "coin": coin,
            "side": side,
            "pnl": round(net, 4),
            "pnl_pct": round(net / (notional / 3) * 100, 4),
            "duration_seconds": int(hold),
            "entry_price": entry_px,
            "exit_price": exit_px,
            "gross_pnl": round(gross, 4),
            "fee": round(fee, 4),
            "exit_type": reason,
            "exit_time": exit_dt,
            "score_total": 0,
            "trend_score": 0,
            "micro_score": 0,
            "mode": "REPLAY",
            "entry_tag": "momentum_replay",
            "regime": "",
            "leverage": 3,
            "notional": notional / 3,
            "mfe_pct": round(mfe, 4),
            "mae_pct": round(mae, 4),
            "high_water_mark": hwm,
            "outcome": "WIN" if net > 0 else "LOSS",
            "fee_killed": gross > 0 and net <= 0,
            "score_bucket": "REPLAY",
            "features": {},
        }

    def get_data_summary(self) -> dict:
        """Summary of available historical data."""
        trade_dir = os.path.join(self._data_dir, "trade")
        if not os.path.exists(trade_dir):
            return {"status": "NO_DATA", "coins": 0, "files": 0, "dates": []}

        coins = 0
        files = 0
        dates = set()
        for coin_dir in os.listdir(trade_dir):
            coin_path = os.path.join(trade_dir, coin_dir)
            if not os.path.isdir(coin_path):
                continue
            coins += 1
            for pq_file in glob.glob(os.path.join(coin_path, "**/*.parquet"), recursive=True):
                files += 1
                parts = pq_file.replace(coin_path, "").strip(os.sep).split(os.sep)
                if parts:
                    dates.add(parts[0])

        return {
            "status": "OK" if files > 0 else "NO_DATA",
            "coins": coins,
            "files": files,
            "dates": sorted(dates),
            "days": len(dates),
        }
