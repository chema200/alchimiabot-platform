"""Metrics Collector: centralized KPI tracking for the platform.

Collects live metrics from all components and exposes them for
dashboards, alerts, and drift detection.
"""

from dataclasses import dataclass, field
from collections import defaultdict
from typing import Any
import time


@dataclass
class TimeseriesPoint:
    timestamp: float
    value: float


class MetricsCollector:
    """Collects and aggregates platform metrics."""

    def __init__(self, retention_sec: int = 3600) -> None:
        self._gauges: dict[str, float] = {}
        self._counters: dict[str, float] = defaultdict(float)
        self._timeseries: dict[str, list[TimeseriesPoint]] = defaultdict(list)
        self._retention = retention_sec

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge value (current state)."""
        key = self._key(name, labels)
        self._gauges[key] = value
        self._timeseries[key].append(TimeseriesPoint(time.time(), value))
        self._trim(key)

    def increment(self, name: str, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increment a counter."""
        key = self._key(name, labels)
        self._counters[key] += amount

    def get_gauge(self, name: str, labels: dict[str, str] | None = None) -> float:
        return self._gauges.get(self._key(name, labels), 0.0)

    def get_counter(self, name: str, labels: dict[str, str] | None = None) -> float:
        return self._counters.get(self._key(name, labels), 0.0)

    def get_timeseries(self, name: str, labels: dict[str, str] | None = None,
                       last_sec: int = 300) -> list[dict]:
        key = self._key(name, labels)
        cutoff = time.time() - last_sec
        return [{"t": p.timestamp, "v": p.value}
                for p in self._timeseries.get(key, []) if p.timestamp >= cutoff]

    def snapshot(self) -> dict[str, Any]:
        """Full snapshot of all metrics."""
        return {
            "gauges": dict(self._gauges),
            "counters": dict(self._counters),
            "timeseries_keys": list(self._timeseries.keys()),
        }

    def _key(self, name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def _trim(self, key: str) -> None:
        cutoff = time.time() - self._retention
        ts = self._timeseries[key]
        while ts and ts[0].timestamp < cutoff:
            ts.pop(0)
