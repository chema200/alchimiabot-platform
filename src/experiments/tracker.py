"""Experiment Tracking: register, run, compare, promote strategies.

Every change to the trading logic is an experiment with a hypothesis.
This system ensures reproducibility and prevents untracked changes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
import os

import structlog

logger = structlog.get_logger()


@dataclass
class ExperimentRecord:
    name: str
    hypothesis: str
    params: dict[str, Any]
    baseline_params: dict[str, Any] | None = None
    status: str = "created"     # created, running, completed, promoted, rejected
    results: dict[str, Any] | None = None
    baseline_results: dict[str, Any] | None = None
    promoted: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "hypothesis": self.hypothesis,
            "status": self.status,
            "promoted": self.promoted,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "params": self.params,
            "results": self.results,
            "baseline_results": self.baseline_results,
            "notes": self.notes,
        }


class ExperimentTracker:
    """Manages experiment lifecycle."""

    def __init__(self, storage_dir: str = "data/experiments") -> None:
        self._dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        self._experiments: dict[str, ExperimentRecord] = {}
        self._load_existing()

    def create(self, name: str, hypothesis: str, params: dict,
               baseline_params: dict | None = None) -> ExperimentRecord:
        """Register a new experiment."""
        if name in self._experiments:
            raise ValueError(f"Experiment '{name}' already exists")

        exp = ExperimentRecord(
            name=name, hypothesis=hypothesis,
            params=params, baseline_params=baseline_params,
        )
        self._experiments[name] = exp
        self._save(exp)
        logger.info("experiment.created", name=name, hypothesis=hypothesis)
        return exp

    def start(self, name: str) -> None:
        exp = self._get(name)
        exp.status = "running"
        self._save(exp)
        logger.info("experiment.started", name=name)

    def complete(self, name: str, results: dict, baseline_results: dict | None = None,
                 notes: str = "") -> ExperimentRecord:
        """Record experiment results."""
        exp = self._get(name)
        exp.status = "completed"
        exp.results = results
        exp.baseline_results = baseline_results
        exp.completed_at = datetime.now(timezone.utc).isoformat()
        exp.notes = notes
        self._save(exp)
        logger.info("experiment.completed", name=name, net_pnl=results.get("net_pnl"))
        return exp

    def promote(self, name: str) -> None:
        """Promote experiment to production."""
        exp = self._get(name)
        if exp.status != "completed":
            raise ValueError(f"Can only promote completed experiments, got: {exp.status}")
        exp.status = "promoted"
        exp.promoted = True
        self._save(exp)
        logger.info("experiment.promoted", name=name)

    def reject(self, name: str, reason: str = "") -> None:
        """Reject experiment."""
        exp = self._get(name)
        exp.status = "rejected"
        exp.notes = reason or exp.notes
        self._save(exp)
        logger.info("experiment.rejected", name=name, reason=reason)

    def compare(self, name: str) -> dict[str, Any] | None:
        """Compare experiment results vs baseline."""
        exp = self._get(name)
        if not exp.results:
            return None

        comparison = {"experiment": exp.results}
        if exp.baseline_results:
            comparison["baseline"] = exp.baseline_results
            comparison["delta"] = {
                k: round(exp.results.get(k, 0) - exp.baseline_results.get(k, 0), 4)
                for k in ["net_pnl", "win_rate", "sharpe", "max_drawdown", "profit_factor"]
                if k in exp.results
            }
        return comparison

    def list_experiments(self, status: str | None = None) -> list[ExperimentRecord]:
        exps = list(self._experiments.values())
        if status:
            exps = [e for e in exps if e.status == status]
        return sorted(exps, key=lambda e: e.created_at, reverse=True)

    def _get(self, name: str) -> ExperimentRecord:
        if name not in self._experiments:
            raise KeyError(f"Experiment '{name}' not found")
        return self._experiments[name]

    def _save(self, exp: ExperimentRecord) -> None:
        path = os.path.join(self._dir, f"{exp.name}.json")
        with open(path, "w") as f:
            json.dump(exp.to_dict(), f, indent=2)

    def _load_existing(self) -> None:
        for file in os.listdir(self._dir):
            if not file.endswith(".json"):
                continue
            try:
                with open(os.path.join(self._dir, file)) as f:
                    data = json.load(f)
                exp = ExperimentRecord(**{k: v for k, v in data.items()
                                         if k in ExperimentRecord.__dataclass_fields__})
                self._experiments[exp.name] = exp
            except Exception:
                logger.warning("experiment.load_error", file=file)
