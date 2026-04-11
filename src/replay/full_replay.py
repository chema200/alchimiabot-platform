"""Full Replay: end-to-end replay that mirrors live pipeline.

Reads raw parquet → feeds EventBus → FeatureStore computes features →
SignalEngine scans → PolicyEngine decides → positions tracked → results stored.

This is THE tool for answering: "if I had run this config on historical data,
what would have happened?"
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from .replay_engine import ReplayEngine, ReplayConfig
from ..ingestion.events import Event, EventType
from ..ingestion.event_bus import EventBus
from ..features.store import FeatureStore
from ..features.contract import FEATURE_VERSION
from ..regime.detector import RegimeDetector
from ..regime.evaluator import RegimeEvaluator
from ..engine.signal.signal_engine import SignalEngine
from ..engine.policy.policy_engine import PolicyEngine, PolicyConfig
from ..engine.sizing.sizing_engine import SizingEngine
from ..engine.risk.risk_manager import RiskManager, RiskConfig
from ..engine.position.position_manager import PositionManager

logger = structlog.get_logger()


@dataclass
class ReplayResult:
    """Complete replay output."""
    # Summary
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: float = 0.0
    gross_pnl: float = 0.0
    total_fees: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    # Context
    events_processed: int = 0
    signals_evaluated: int = 0
    signals_entered: int = 0
    signals_skipped: int = 0
    elapsed_sec: float = 0.0
    feature_version: str = ""
    config: dict = field(default_factory=dict)
    # Detail
    trades: list[dict] = field(default_factory=list)
    evaluations: list[dict] = field(default_factory=list)
    pnl_curve: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 4),
            "net_pnl": round(self.net_pnl, 4),
            "gross_pnl": round(self.gross_pnl, 4),
            "total_fees": round(self.total_fees, 4),
            "avg_win": round(self.avg_win, 4),
            "avg_loss": round(self.avg_loss, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe": round(self.sharpe, 4),
            "profit_factor": round(self.profit_factor, 4),
            "events_processed": self.events_processed,
            "signals_evaluated": self.signals_evaluated,
            "signals_entered": self.signals_entered,
            "signals_skipped": self.signals_skipped,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "feature_version": self.feature_version,
        }


class FullReplay:
    """End-to-end replay with full pipeline."""

    def __init__(self, replay_config: ReplayConfig, policy_config: PolicyConfig | None = None,
                 risk_config: RiskConfig | None = None, budget: float = 1000.0,
                 leverage: int = 3, scan_every_n: int = 50) -> None:
        self._replay_config = replay_config
        self._policy_config = policy_config or PolicyConfig()
        self._risk_config = risk_config or RiskConfig()
        self._budget = budget
        self._leverage = leverage
        self._scan_every_n = scan_every_n

    async def run(self) -> ReplayResult:
        """Execute full replay."""
        import time as _time
        import numpy as np

        start_time = _time.time()

        # Build pipeline
        event_bus = EventBus()
        feature_store = FeatureStore(event_bus)
        regime_detector = RegimeDetector(feature_store)
        regime_evaluator = RegimeEvaluator()
        signal_engine = SignalEngine(feature_store, regime_detector)
        policy_engine = PolicyEngine(self._policy_config, regime_detector, regime_evaluator)
        sizing_engine = SizingEngine(self._budget, self._leverage)
        risk_manager = RiskManager(self._risk_config)
        position_manager = PositionManager()

        await feature_store.start()

        # Replay state
        coins = self._replay_config.coins or ["BTC", "ETH", "SOL"]
        event_count = 0
        all_trades: list[dict] = []
        all_evals: list[dict] = []
        pnl_curve: list[float] = []
        cumulative_pnl = 0.0

        async def on_event(event: Event) -> None:
            nonlocal event_count, cumulative_pnl

            # Feed to feature store through bus
            await event_bus.publish(event)
            # Process immediately (single-threaded replay)
            try:
                ev = event_bus._queue.get_nowait()
                for sub in event_bus._subscribers.get(ev.type, []):
                    await sub.handler(ev)
                for sub in event_bus._subscribers.get(None, []):
                    await sub.handler(ev)
            except Exception:
                pass

            event_count += 1

            # Update positions
            if event.type == EventType.TRADE:
                mid = event.data.get("price", 0.0)
                to_close = position_manager.update_price(event.coin, mid)

                for pos in to_close:
                    result = position_manager.close(pos, mid)
                    all_trades.append(result)
                    cumulative_pnl += result["net_pnl"]
                    pnl_curve.append(cumulative_pnl)
                    policy_engine.register_exit(pos.coin, pos.size_usd)
                    risk_manager.record_trade_result(result["net_pnl"], pos.exit_reason == "SL")
                    regime_evaluator.record_trade(
                        pos.coin, regime_detector.detect(pos.coin).regime,
                        result["net_pnl"], result["fees"], pos.hold_sec, result["net_pnl"] > 0)

            # Scan for entries
            if event_count % self._scan_every_n == 0:
                if risk_manager.is_sl_guard_active():
                    return

                signals = signal_engine.scan(coins, min_score=0.2)
                for signal in signals[:3]:
                    decision = policy_engine.evaluate(signal)
                    all_evals.append({
                        "coin": signal.coin, "side": signal.side,
                        "score": signal.score, "action": decision.action,
                        "reason": decision.reason, "regime": signal.regime.value,
                    })

                    if decision.action != "ENTER":
                        continue

                    # Get price
                    last_price = 0.0
                    trades_deque = feature_store._trades.get(signal.coin)
                    if trades_deque:
                        for t in reversed(list(trades_deque)):
                            last_price = t.get("price", 0)
                            if last_price > 0:
                                break
                    if last_price <= 0:
                        continue

                    size = sizing_engine.calculate(decision, last_price)
                    if not size:
                        continue

                    regime_state = regime_detector.detect(signal.coin)
                    risk_levels = risk_manager.calculate_levels(
                        signal.coin, signal.side, last_price,
                        feature_store.get_snapshot(signal.coin).features,
                        regime_state.regime)

                    position_manager.open(
                        signal.coin, signal.side, last_price,
                        size.size_coins, size.size_usd, size.leverage,
                        risk_levels, signal.score, signal.trend_score,
                        signal.micro_score, regime_state.regime.value)
                    policy_engine.register_entry(signal.coin, signal.side, size.size_usd)

        # Run replay
        replay = ReplayEngine(self._replay_config)
        replay.on_event(on_event)
        replay_stats = await replay.run()

        # Close remaining positions
        for pos in list(position_manager.open_positions):
            if pos.current_price > 0:
                result = position_manager.close(pos, pos.current_price, "END_OF_DATA")
                all_trades.append(result)
                cumulative_pnl += result["net_pnl"]
                pnl_curve.append(cumulative_pnl)

        elapsed = _time.time() - start_time

        # Compile results
        result = ReplayResult()
        result.total_trades = len(all_trades)
        result.events_processed = event_count
        result.signals_evaluated = len(all_evals)
        result.signals_entered = sum(1 for e in all_evals if e["action"] == "ENTER")
        result.signals_skipped = sum(1 for e in all_evals if e["action"] != "ENTER")
        result.elapsed_sec = elapsed
        result.feature_version = FEATURE_VERSION
        result.trades = all_trades
        result.evaluations = all_evals
        result.pnl_curve = pnl_curve

        if all_trades:
            pnls = [t["net_pnl"] for t in all_trades]
            fees = [t["fees"] for t in all_trades]
            wins_list = [p for p in pnls if p > 0]
            losses_list = [p for p in pnls if p <= 0]

            result.wins = len(wins_list)
            result.losses = len(losses_list)
            result.net_pnl = sum(pnls)
            result.gross_pnl = sum(t["gross_pnl"] for t in all_trades)
            result.total_fees = sum(fees)
            result.win_rate = result.wins / len(pnls) if pnls else 0
            result.avg_win = np.mean(wins_list) if wins_list else 0
            result.avg_loss = np.mean(losses_list) if losses_list else 0

            # Max drawdown
            cumul = np.cumsum(pnls)
            peak = np.maximum.accumulate(cumul)
            result.max_drawdown = float(np.max(peak - cumul)) if len(cumul) > 0 else 0

            # Sharpe
            if len(pnls) > 1:
                std = np.std(pnls)
                if std > 0:
                    result.sharpe = float(np.mean(pnls) / std * np.sqrt(252 * 288))

            # Profit factor
            gross_profit = sum(wins_list)
            gross_loss = abs(sum(losses_list))
            result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        logger.info("full_replay.completed", **result.to_dict())
        return result
