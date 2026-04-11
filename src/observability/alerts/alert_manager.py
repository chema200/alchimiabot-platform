"""Alert Manager: monitors metrics and triggers alerts.

Checks conditions periodically and notifies when thresholds are breached.
Supports cooldowns to prevent alert spam.
"""

from dataclasses import dataclass, field
from typing import Any, Callable
import time

import structlog

from ..metrics.collector import MetricsCollector

logger = structlog.get_logger()


@dataclass
class AlertRule:
    name: str
    metric: str
    condition: str          # "above", "below", "equals"
    threshold: float
    severity: str = "warning"   # info, warning, critical
    cooldown_sec: int = 300
    labels: dict[str, str] | None = None


@dataclass
class Alert:
    rule: str
    severity: str
    message: str
    value: float
    threshold: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "value": round(self.value, 4),
            "threshold": self.threshold,
            "timestamp": self.timestamp,
        }


class AlertManager:
    """Evaluates alert rules against metrics."""

    def __init__(self, metrics: MetricsCollector) -> None:
        self._metrics = metrics
        self._rules: list[AlertRule] = []
        self._last_fired: dict[str, float] = {}
        self._history: list[Alert] = []
        self._handlers: list[Callable[[Alert], None]] = []
        self._max_history = 500

    def add_rule(self, rule: AlertRule) -> None:
        self._rules.append(rule)

    def on_alert(self, handler: Callable[[Alert], None]) -> None:
        """Register alert handler (e.g., Telegram, log)."""
        self._handlers.append(handler)

    def check(self) -> list[Alert]:
        """Evaluate all rules and return fired alerts."""
        fired = []
        now = time.time()

        for rule in self._rules:
            # Cooldown check
            last = self._last_fired.get(rule.name, 0)
            if now - last < rule.cooldown_sec:
                continue

            value = self._metrics.get_gauge(rule.metric, rule.labels)
            triggered = False

            if rule.condition == "above" and value > rule.threshold:
                triggered = True
            elif rule.condition == "below" and value < rule.threshold:
                triggered = True
            elif rule.condition == "equals" and value == rule.threshold:
                triggered = True

            if triggered:
                alert = Alert(
                    rule=rule.name,
                    severity=rule.severity,
                    message=f"{rule.name}: {rule.metric} is {value:.4f} ({rule.condition} {rule.threshold})",
                    value=value,
                    threshold=rule.threshold,
                )
                fired.append(alert)
                self._last_fired[rule.name] = now
                self._history.append(alert)
                if len(self._history) > self._max_history:
                    self._history.pop(0)

                for handler in self._handlers:
                    try:
                        handler(alert)
                    except Exception:
                        logger.exception("alert.handler_error", rule=rule.name)

        return fired

    @property
    def recent_alerts(self) -> list[dict]:
        return [a.to_dict() for a in self._history[-20:]]

    def setup_defaults(self) -> None:
        """Add standard alert rules."""
        self.add_rule(AlertRule("high_drawdown", "portfolio.drawdown_pct", "above", 3.0, "critical"))
        self.add_rule(AlertRule("sl_guard", "trading.sl_count_30m", "above", 4, "warning"))
        self.add_rule(AlertRule("ws_disconnected", "ingestion.hl_ws_connected", "equals", 0, "critical", cooldown_sec=60))
        self.add_rule(AlertRule("high_latency", "ingestion.latency_ms", "above", 500, "warning"))
        self.add_rule(AlertRule("low_fill_rate", "execution.fill_rate", "below", 0.9, "warning"))
