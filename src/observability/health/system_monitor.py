"""System Monitor: disk usage, growth tracking, resource health.

Tracks disk space, data growth rates, and estimates time to full.
Triggers alerts at configurable thresholds.
"""

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class DirUsage:
    path: str
    size_bytes: int
    file_count: int

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 ** 3)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "size_gb": round(self.size_gb, 3),
            "file_count": self.file_count,
        }


class SystemMonitor:
    """Monitors disk usage and data growth."""

    def __init__(self, data_dirs: dict[str, str] | None = None,
                 warn_pct: float = 70, alert_pct: float = 85, critical_pct: float = 92) -> None:
        self._data_dirs = data_dirs or {
            "raw": "data/raw",
            "processed": "data/processed",
            "datasets": "data/datasets",
            "logs": "logs",
        }
        self._warn_pct = warn_pct
        self._alert_pct = alert_pct
        self._critical_pct = critical_pct
        self._history: list[dict] = []  # [{timestamp, total_gb, used_gb}]
        self._max_history = 1440  # 24h at 1/min

    def get_disk_status(self) -> dict[str, Any]:
        """Full disk health report."""
        usage = shutil.disk_usage("/")
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        pct = usage.used / usage.total * 100

        # Severity
        if pct >= self._critical_pct:
            severity = "critical"
        elif pct >= self._alert_pct:
            severity = "alert"
        elif pct >= self._warn_pct:
            severity = "warning"
        else:
            severity = "ok"

        # Track history for growth estimation
        now = time.time()
        self._history.append({"timestamp": now, "used_gb": used_gb})
        while len(self._history) > self._max_history:
            self._history.pop(0)

        # Growth estimates
        growth_24h = self._estimate_growth(86400)
        growth_7d = self._estimate_growth(604800)
        days_remaining = free_gb / (growth_24h / 1) if growth_24h > 0.001 else 9999

        # Per-directory usage
        dir_usage = {}
        for name, path in self._data_dirs.items():
            if os.path.exists(path):
                dir_usage[name] = self._measure_dir(path).to_dict()
            else:
                dir_usage[name] = {"path": path, "size_gb": 0, "file_count": 0}

        # PostgreSQL size (approximate)
        pg_size = self._get_pg_volume_size()
        if pg_size:
            dir_usage["postgres"] = pg_size

        return {
            "disk": {
                "total_gb": round(total_gb, 2),
                "used_gb": round(used_gb, 2),
                "free_gb": round(free_gb, 2),
                "usage_pct": round(pct, 1),
                "severity": severity,
            },
            "growth": {
                "growth_24h_gb": round(growth_24h, 4),
                "growth_7d_gb": round(growth_7d, 4),
                "estimated_days_remaining": round(days_remaining, 0),
            },
            "directories": dir_usage,
        }

    def check_alerts(self) -> list[dict]:
        """Return active disk alerts."""
        status = self.get_disk_status()
        alerts = []
        pct = status["disk"]["usage_pct"]

        if pct >= self._critical_pct:
            alerts.append({"severity": "critical", "message": f"Disk {pct:.1f}% full — CRITICAL"})
        elif pct >= self._alert_pct:
            alerts.append({"severity": "alert", "message": f"Disk {pct:.1f}% full"})
        elif pct >= self._warn_pct:
            alerts.append({"severity": "warning", "message": f"Disk {pct:.1f}% full"})

        # Check individual dirs for abnormal growth
        growth = status["growth"]["growth_24h_gb"]
        if growth > 10:  # > 10 GB/day is suspicious
            alerts.append({"severity": "warning", "message": f"Abnormal growth: {growth:.1f} GB/day"})

        return alerts

    def _estimate_growth(self, window_sec: int) -> float:
        """Estimate GB growth over a time window."""
        if len(self._history) < 2:
            return 0.0

        now = time.time()
        cutoff = now - window_sec
        old_points = [h for h in self._history if h["timestamp"] <= cutoff]

        if not old_points:
            # Use oldest available point
            oldest = self._history[0]
            newest = self._history[-1]
            elapsed = newest["timestamp"] - oldest["timestamp"]
            if elapsed < 60:
                return 0.0
            growth_per_sec = (newest["used_gb"] - oldest["used_gb"]) / elapsed
            return growth_per_sec * window_sec

        old = old_points[-1]
        new = self._history[-1]
        elapsed = new["timestamp"] - old["timestamp"]
        if elapsed < 60:
            return 0.0
        growth_per_sec = (new["used_gb"] - old["used_gb"]) / elapsed
        return growth_per_sec * window_sec

    @staticmethod
    def _measure_dir(path: str) -> DirUsage:
        """Measure total size and file count of a directory."""
        total_size = 0
        file_count = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total_size += os.path.getsize(fp)
                    file_count += 1
                except OSError:
                    pass
        return DirUsage(path=path, size_bytes=total_size, file_count=file_count)

    @staticmethod
    def _get_pg_volume_size() -> dict | None:
        """Try to get PostgreSQL data size from Docker volume."""
        try:
            import subprocess
            result = subprocess.run(
                ["docker", "exec", "platform-postgres", "du", "-sb", "/var/lib/postgresql/data"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                size_bytes = int(result.stdout.split()[0])
                return {"path": "docker:platform-postgres", "size_gb": round(size_bytes / (1024**3), 3), "file_count": -1}
        except Exception:
            pass
        return None
