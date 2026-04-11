"""Storage Check: monitors disk usage, parquet health, data growth."""

import os
import shutil
from datetime import datetime, timezone

from ..base import AuditCheck, CheckResult


class StorageCheck(AuditCheck):
    name = "storage"
    audit_type = "storage"

    def __init__(self, data_dirs: dict[str, str] | None = None) -> None:
        self._dirs = data_dirs or {
            "raw": "data/raw",
            "processed": "data/processed",
            "datasets": "data/datasets",
            "logs": "logs",
        }

    async def run(self) -> CheckResult:
        result = CheckResult(summary="Storage and disk health check")

        # 1. Disk usage
        usage = shutil.disk_usage("/")
        pct = usage.used / usage.total * 100
        result.metrics["disk_total_gb"] = round(usage.total / 1e9, 2)
        result.metrics["disk_used_gb"] = round(usage.used / 1e9, 2)
        result.metrics["disk_free_gb"] = round(usage.free / 1e9, 2)
        result.metrics["disk_pct"] = round(pct, 1)

        if pct >= 92:
            result.add_finding("critical", "DISK_CRITICAL", f"Disk {pct:.1f}% full")
        elif pct >= 85:
            result.add_finding("error", "DISK_HIGH", f"Disk {pct:.1f}% full")
        elif pct >= 70:
            result.add_finding("warning", "DISK_WARNING", f"Disk {pct:.1f}% full")

        # 2. Per-directory sizes
        for name, path in self._dirs.items():
            if os.path.exists(path):
                size_bytes, file_count = self._dir_stats(path)
                result.metrics[f"dir_{name}_gb"] = round(size_bytes / 1e9, 4)
                result.metrics[f"dir_{name}_files"] = file_count
            else:
                result.metrics[f"dir_{name}_gb"] = 0
                result.metrics[f"dir_{name}_files"] = 0

        # 3. Parquet file count today
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_files = 0
        raw_dir = self._dirs.get("raw", "data/raw")
        if os.path.exists(raw_dir):
            for root, dirs, files in os.walk(raw_dir):
                if today in root:
                    today_files += sum(1 for f in files if f.endswith(".parquet"))
        result.metrics["parquet_files_today"] = today_files

        # 4. Empty date partitions (possible gap)
        # Check if yesterday exists
        from datetime import timedelta
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_files = 0
        if os.path.exists(raw_dir):
            for root, dirs, files in os.walk(raw_dir):
                if yesterday in root:
                    yesterday_files += sum(1 for f in files if f.endswith(".parquet"))
        result.metrics["parquet_files_yesterday"] = yesterday_files
        if yesterday_files == 0 and today_files > 0:
            result.add_finding("warning", "MISSING_YESTERDAY", "No parquet files for yesterday — possible gap")

        return result

    @staticmethod
    def _dir_stats(path: str) -> tuple[int, int]:
        total_size = 0
        file_count = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                try:
                    total_size += os.path.getsize(os.path.join(dirpath, f))
                    file_count += 1
                except OSError:
                    pass
        return total_size, file_count
