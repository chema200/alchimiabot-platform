"""Backtesting Framework: runs strategies against historical data.

Orchestrates replay + feature store + signal + policy + risk + positions
to simulate trading and produce performance metrics.
"""

from dataclasses import dataclass, field
from typing import Any

import structlog

from ..replay.replay_engine import ReplayEngine, ReplayConfig
from ..features.store import FeatureStore
from ..features.base import FeatureSnapshot
from ..regime.detector import RegimeDetector
from ..regime.evaluator import RegimeEvaluator
from ..engine.signal.signal_engine import SignalEngine
from ..engine.policy.policy_engine import PolicyEngine, PolicyConfig
from ..engine.sizing.sizing_engine import SizingEngine
from ..engine.risk.risk_manager import RiskManager, RiskConfig
from ..engine.position.position_manager import PositionManager
from ..engine.execution.execution_simulator import ExecutionSimulator
from ..ingestion.events import Event, EventType
from ..ingestion.event_bus import EventBus

logger = structlog.get_logger()


@dataclass
class BacktestConfig:
    replay: ReplayConfig = field(default_factory=ReplayConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    budget: float = 1000.0
    leverage: int = 3
    scan_interval_events: int = 100    # scan for signals every N events


@dataclass
class BacktestResult:
    """Complete backtest performance report."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    avg_hold_sec: float = 0.0
    events_processed: int = 0
    trades: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 4),
            "total_pnl": round(self.total_pnl, 4),
            "total_fees": round(self.total_fees, 4),
            "net_pnl": round(self.net_pnl, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "avg_pnl": round(self.avg_pnl, 4),
            "sharpe": round(self.sharpe, 4),
            "profit_factor": round(self.profit_factor, 4),
            "avg_hold_sec": round(self.avg_hold_sec, 1),
            "events_processed": self.events_processed,
        }


class BacktestRunner:
    """Runs a complete backtest simulation."""

    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        self._event_count = 0

    async def run(self) -> BacktestResult:
        """Execute the backtest and return results."""
        cfg = self._config

        # Build the pipeline
        event_bus = EventBus()
        feature_store = FeatureStore(event_bus)
        regime_detector = RegimeDetector(feature_store)
        regime_evaluator = RegimeEvaluator()
        signal_engine = SignalEngine(feature_store, regime_detector)
        policy_engine = PolicyEngine(cfg.policy, regime_detector, regime_evaluator)
        sizing_engine = SizingEngine(cfg.budget, cfg.leverage)
        risk_manager = RiskManager(cfg.risk)
        position_manager = PositionManager()
        exec_sim = ExecutionSimulator()

        # Replay engine
        replay = ReplayEngine(cfg.replay)

        # Track results
        pnl_curve: list[float] = []
        all_trades: list[dict] = []
        coins = cfg.replay.coins or ["BTC", "ETH", "SOL"]

        async def on_event(event: Event) -> None:
            self._event_count += 1

            # Feed event to feature store via bus
            await event_bus.publish(event)

            # Process the event in the bus (single-threaded for determinism)
            if not event_bus._queue.empty():
                ev = event_bus._queue.get_nowait()
                for handler in event_bus._subscribers.get(ev.type, []):
                    await handler(ev)
                for handler in event_bus._subscribers.get(None, []):
                    await handler(ev)

            # Update positions with new price
            if event.type == EventType.TRADE:
                mid = event.data.get("price", 0.0)
                to_close = position_manager.update_price(event.coin, mid)
                for pos in to_close:
                    result = position_manager.close(pos, mid)
                    all_trades.append(result)
                    pnl_curve.append(result["net_pnl"])
                    policy_engine.register_exit(pos.coin, pos.size_usd)
                    risk_manager.record_trade_result(result["net_pnl"], pos.exit_reason == "SL")
                    regime_evaluator.record_trade(
                        pos.coin, regime_detector.detect(pos.coin).regime,
                        result["net_pnl"], result["fees"], pos.hold_sec, result["net_pnl"] > 0)

            # Scan for entries periodically
            if self._event_count % cfg.scan_interval_events == 0:
                signals = signal_engine.scan(coins, min_score=0.2)
                for signal in signals[:2]:  # max 2 signals per scan
                    decision = policy_engine.evaluate(signal)
                    if decision.action != "ENTER":
                        continue

                    snap = feature_store.get_snapshot(signal.coin)
                    price = snap.features.get("mom_ret_1m", 0)  # dummy, use last trade price
                    # Get actual price from recent trades
                    last_price = 0.0
                    for t in reversed(list(feature_store._trades.get(signal.coin, []))):
                        last_price = t.get("price", 0)
                        break
                    if last_price <= 0:
                        continue

                    size = sizing_engine.calculate(decision, last_price)
                    if not size:
                        continue

                    regime_state = regime_detector.detect(signal.coin)
                    risk_levels = risk_manager.calculate_levels(
                        signal.coin, signal.side, last_price,
                        snap.features, regime_state.regime)

                    # Simulate execution
                    exec_result = exec_sim.simulate_ioc(
                        signal.coin, signal.is_long, last_price, size.size_coins)
                    if not exec_result.filled:
                        continue

                    position_manager.open(
                        signal.coin, signal.side, exec_result.fill_price,
                        exec_result.fill_size, size.size_usd, size.leverage,
                        risk_levels, signal.score, signal.trend_score,
                        signal.micro_score, regime_state.regime.value)
                    policy_engine.register_entry(signal.coin, signal.side, size.size_usd)

        # Wire up and run
        await feature_store.start()
        replay.on_event(on_event)
        replay_stats = await replay.run()

        # Close remaining positions at last price
        for pos in list(position_manager.open_positions):
            if pos.current_price > 0:
                result = position_manager.close(pos, pos.current_price, "END_OF_DATA")
                all_trades.append(result)
                pnl_curve.append(result["net_pnl"])

        return self._compile_results(all_trades, pnl_curve, replay_stats.events_replayed)

    def _compile_results(self, trades: list[dict], pnl_curve: list[float],
                         events: int) -> BacktestResult:
        """Compile trade list into summary statistics."""
        import numpy as np

        result = BacktestResult()
        result.total_trades = len(trades)
        result.events_processed = events
        result.trades = trades

        if not trades:
            return result

        pnls = [t["net_pnl"] for t in trades]
        fees = [t["fees"] for t in trades]
        holds = [t["hold_sec"] for t in trades]

        result.wins = sum(1 for p in pnls if p > 0)
        result.losses = sum(1 for p in pnls if p <= 0)
        result.total_pnl = sum(pnls)
        result.total_fees = sum(fees)
        result.net_pnl = result.total_pnl
        result.win_rate = result.wins / len(pnls) if pnls else 0
        result.avg_pnl = np.mean(pnls) if pnls else 0
        result.avg_hold_sec = np.mean(holds) if holds else 0

        # Max drawdown
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdowns = peak - cumulative
        result.max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0

        # Sharpe (annualized, assuming 5min intervals)
        if len(pnls) > 1:
            std = np.std(pnls)
            if std > 0:
                result.sharpe = float(np.mean(pnls) / std * np.sqrt(252 * 288))  # 288 5-min periods/day

        # Profit factor
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return result
