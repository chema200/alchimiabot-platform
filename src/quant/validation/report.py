"""Validation Report: structured output of validation results."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class BatchVerdict:
    decision: str  # ADOPT, TEST_LIVE, REJECT, INCONCLUSIVE, BLOCKED_BY_DATA_QUALITY, INVALID
    reasoning: str
    risk_flags: list[str] = field(default_factory=list)
    confidence: str = "LOW"  # LOW, MEDIUM, HIGH

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reasoning": self.reasoning,
            "risk_flags": self.risk_flags,
            "confidence": self.confidence,
        }


@dataclass
class BatchResult:
    batch_name: str
    hypothesis: str
    experiments: list[dict]
    best_experiment: str
    verdict: BatchVerdict
    batch_status_flags: list[str] = field(default_factory=list)
    data_quality_blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "batch_name": self.batch_name,
            "hypothesis": self.hypothesis,
            "experiments": self.experiments,
            "best_experiment": self.best_experiment,
            "verdict": self.verdict.to_dict(),
            "batch_status_flags": self.batch_status_flags,
            "data_quality_blockers": self.data_quality_blockers,
        }


@dataclass
class ValidationReport:
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_trades_baseline: int = 0
    baseline_summary: dict = field(default_factory=dict)
    batches: list[BatchResult] = field(default_factory=list)
    combination_suggestion: dict | None = None
    validation_integrity: dict = field(default_factory=lambda: {"status": "OK", "issues": []})

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "total_trades_baseline": self.total_trades_baseline,
            "baseline_summary": self.baseline_summary,
            "batches": [b.to_dict() for b in self.batches],
            "combination_suggestion": self.combination_suggestion,
            "validation_integrity": self.validation_integrity,
        }
