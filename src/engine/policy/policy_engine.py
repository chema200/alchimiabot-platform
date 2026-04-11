"""Policy Engine: decides whether to act on a signal.

The Signal Engine finds opportunities. The Policy Engine decides:
- Should we enter? (filters, blocks, regime rules)
- How much? (delegates to Sizing)
- Any adjustments? (boost, reduce, coin blocks)

Separating signal from policy enables independent testing and tuning.
"""

from dataclasses import dataclass, field
from typing import Any
import time

import structlog

from ..signal.signal_engine import Signal
from ...regime.detector import Regime, RegimeDetector
from ...regime.evaluator import RegimeEvaluator

logger = structlog.get_logger()


@dataclass
class PolicyConfig:
    """Policy rules — all tunable."""
    min_signal_score: float = 0.35
    max_positions: int = 3
    max_positions_per_coin: int = 1
    max_exposure_usd: float = 500.0
    max_correlation_overlap: int = 2    # max coins from same sector
    cooldown_sec: int = 300             # per-coin cooldown after exit

    # Regime-specific overrides
    regime_blocks: list[str] = field(default_factory=lambda: ["quiet"])  # block entries in these regimes
    regime_reduce: dict[str, float] = field(default_factory=lambda: {"choppy": 0.5, "high_vol": 0.7})
    regime_boost: dict[str, float] = field(default_factory=lambda: {"trending_up": 1.2, "trending_down": 1.2})

    # Score thresholds per component
    min_trend_score: float = 0.15
    min_micro_score: float = 0.10

    # Weekend / session rules
    weekend_max_positions: int = 1
    weekend_min_score: float = 0.50


@dataclass(slots=True)
class PolicyDecision:
    """Result of policy evaluation."""
    action: str             # ENTER, SKIP, BLOCKED
    signal: Signal
    reason: str
    adjusted_score: float
    size_multiplier: float  # 1.0 = normal, 0.5 = half, 1.2 = boost
    metadata: dict[str, Any] = field(default_factory=dict)


class PolicyEngine:
    """Evaluates signals against policy rules."""

    def __init__(self, config: PolicyConfig, regime_detector: RegimeDetector,
                 regime_evaluator: RegimeEvaluator) -> None:
        self._config = config
        self._regime = regime_detector
        self._regime_eval = regime_evaluator
        self._cooldowns: dict[str, float] = {}  # coin -> expiry timestamp
        self._open_positions: dict[str, str] = {}  # coin -> side
        self._total_exposure_usd: float = 0.0

    def evaluate(self, signal: Signal) -> PolicyDecision:
        """Decide whether to act on a signal."""
        cfg = self._config
        regime = signal.regime

        # 1. Cooldown check
        if self._is_on_cooldown(signal.coin):
            return self._skip(signal, "COOLDOWN")

        # 2. Position limits
        if len(self._open_positions) >= cfg.max_positions:
            return self._skip(signal, "MAX_POSITIONS")

        if signal.coin in self._open_positions:
            return self._skip(signal, "ALREADY_IN_POSITION")

        # 3. Exposure limit
        if self._total_exposure_usd >= cfg.max_exposure_usd:
            return self._skip(signal, "MAX_EXPOSURE")

        # 4. Regime blocks
        if regime.value in cfg.regime_blocks:
            return self._block(signal, f"REGIME_BLOCKED:{regime.value}")

        # 5. Regime historical check
        if not self._regime_eval.should_trade_regime(regime):
            return self._block(signal, f"REGIME_UNPROFITABLE:{regime.value}")

        # 6. Component minimums
        if signal.trend_score < cfg.min_trend_score:
            return self._skip(signal, "LOW_TREND_SCORE")

        if signal.micro_score < cfg.min_micro_score:
            return self._skip(signal, "LOW_MICRO_SCORE")

        # 7. Weekend rules
        is_weekend = signal.features.get("temp_is_weekend", 0.0) > 0
        if is_weekend:
            if len(self._open_positions) >= cfg.weekend_max_positions:
                return self._skip(signal, "WEEKEND_MAX_POSITIONS")
            if signal.score < cfg.weekend_min_score:
                return self._skip(signal, "WEEKEND_LOW_SCORE")

        # 8. Regime adjustments
        adjusted_score = signal.score
        size_mult = 1.0

        if regime.value in cfg.regime_boost:
            boost = cfg.regime_boost[regime.value]
            adjusted_score *= boost
            size_mult *= min(boost, 1.3)

        if regime.value in cfg.regime_reduce:
            reduce = cfg.regime_reduce[regime.value]
            adjusted_score *= reduce
            size_mult *= reduce

        # 9. Final score check
        if adjusted_score < cfg.min_signal_score:
            return self._skip(signal, f"SCORE_TOO_LOW:{adjusted_score:.3f}")

        # ENTER
        return PolicyDecision(
            action="ENTER",
            signal=signal,
            reason=f"SIGNAL_ACCEPTED:{regime.value}",
            adjusted_score=round(adjusted_score, 4),
            size_multiplier=round(size_mult, 3),
            metadata={"regime": regime.value, "is_weekend": is_weekend},
        )

    def register_entry(self, coin: str, side: str, notional_usd: float) -> None:
        """Call after a position is opened."""
        self._open_positions[coin] = side
        self._total_exposure_usd += notional_usd

    def register_exit(self, coin: str, notional_usd: float) -> None:
        """Call after a position is closed."""
        self._open_positions.pop(coin, None)
        self._total_exposure_usd = max(0, self._total_exposure_usd - notional_usd)
        self._cooldowns[coin] = time.time() + self._config.cooldown_sec

    def _is_on_cooldown(self, coin: str) -> bool:
        expiry = self._cooldowns.get(coin, 0)
        if time.time() >= expiry:
            self._cooldowns.pop(coin, None)
            return False
        return True

    def _skip(self, signal: Signal, reason: str) -> PolicyDecision:
        return PolicyDecision("SKIP", signal, reason, signal.score, 0.0)

    def _block(self, signal: Signal, reason: str) -> PolicyDecision:
        return PolicyDecision("BLOCKED", signal, reason, signal.score, 0.0)

    @property
    def state(self) -> dict[str, Any]:
        return {
            "open_positions": dict(self._open_positions),
            "total_exposure_usd": round(self._total_exposure_usd, 2),
            "cooldowns": {c: round(t - time.time()) for c, t in self._cooldowns.items() if t > time.time()},
        }
