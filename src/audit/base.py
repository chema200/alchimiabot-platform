"""Base check interface for the audit system."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Finding:
    severity: str       # info, warning, error, critical
    code: str           # machine-readable code
    message: str        # human-readable message
    entity_type: str = ""
    entity_id: str = ""
    payload: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    status: str = "OK"          # OK, WARNING, ERROR
    score: int = 100            # 0-100
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def add_finding(self, severity: str, code: str, message: str, **kwargs) -> None:
        self.findings.append(Finding(severity=severity, code=code, message=message, **kwargs))
        if severity == "error" or severity == "critical":
            self.status = "ERROR"
            self.score = min(self.score, 40)
        elif severity == "warning" and self.status != "ERROR":
            self.status = "WARNING"
            self.score = min(self.score, 75)


class AuditCheck(ABC):
    """Base class for all audit checks."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def audit_type(self) -> str: ...

    @abstractmethod
    async def run(self) -> CheckResult: ...
