"""Audit Runner: schedules and executes checks, persists results.

Each check type runs on its own interval:
  integration:  every 5 min
  data_quality:  every 15 min
  storage:       every 1 hour
  consistency:   every 6 hours
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from .base import AuditCheck, CheckResult
from ..storage.postgres.models import AuditRun, AuditFinding

logger = structlog.get_logger()


class ScheduledCheck:
    """A check with its schedule configuration."""
    def __init__(self, check: AuditCheck, interval_sec: int) -> None:
        self.check = check
        self.interval_sec = interval_sec
        self.last_result: CheckResult | None = None
        self.last_run: datetime | None = None
        self.run_count = 0
        self.error_count = 0


class AuditRunner:
    """Runs all audit checks on their schedules and persists results."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory
        self._checks: list[ScheduledCheck] = []
        self._running = False
        self._global_score = 100

    def register(self, check: AuditCheck, interval_sec: int) -> None:
        self._checks.append(ScheduledCheck(check, interval_sec))

    async def start(self) -> None:
        """Start all check schedules."""
        self._running = True
        logger.info("audit_runner.started", checks=len(self._checks))

        tasks = []
        for sc in self._checks:
            tasks.append(asyncio.create_task(self._run_loop(sc)))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._running = False
        logger.info("audit_runner.stopped")

    async def _run_loop(self, sc: ScheduledCheck) -> None:
        # Run immediately once, then on schedule
        await self._execute_check(sc)
        while self._running:
            await asyncio.sleep(sc.interval_sec)
            if not self._running:
                break
            await self._execute_check(sc)

    async def _execute_check(self, sc: ScheduledCheck) -> None:
        started = datetime.now(timezone.utc)
        try:
            result = await sc.check.run()
            sc.last_result = result
            sc.last_run = started
            sc.run_count += 1

            finished = datetime.now(timezone.utc)

            # Persist to DB
            await self._persist(sc.check, result, started, finished)

            # Update global score
            self._update_global_score()

            level = "info" if result.status == "OK" else "warning" if result.status == "WARNING" else "error"
            getattr(logger, level)(
                f"audit.{sc.check.name}",
                status=result.status, score=result.score,
                findings=len(result.findings),
            )
        except Exception:
            sc.error_count += 1
            logger.exception(f"audit.{sc.check.name}.error")

    async def _persist(self, check: AuditCheck, result: CheckResult,
                       started: datetime, finished: datetime) -> None:
        try:
            async with self._sf() as session:
                run = AuditRun(
                    audit_type=check.audit_type,
                    status=result.status,
                    score=result.score,
                    started_at=started,
                    finished_at=finished,
                    summary=result.summary,
                    details={"findings_count": len(result.findings)},
                    metrics=result.metrics,
                )
                session.add(run)
                await session.flush()  # get run.id

                for finding in result.findings:
                    session.add(AuditFinding(
                        audit_run_id=run.id,
                        severity=finding.severity,
                        code=finding.code,
                        message=finding.message,
                        entity_type=finding.entity_type or None,
                        entity_id=finding.entity_id or None,
                        payload=finding.payload or None,
                    ))

                await session.commit()
        except Exception:
            logger.exception("audit.persist_error")

    def _update_global_score(self) -> None:
        scores = [sc.last_result.score for sc in self._checks if sc.last_result]
        self._global_score = min(scores) if scores else 100

    async def run_all_now(self) -> dict[str, Any]:
        """Run all checks immediately and return combined report."""
        results = {}
        for sc in self._checks:
            try:
                result = await sc.check.run()
                sc.last_result = result
                sc.last_run = datetime.now(timezone.utc)
                results[sc.check.name] = {
                    "status": result.status,
                    "score": result.score,
                    "summary": result.summary,
                    "findings": [{"severity": f.severity, "code": f.code, "message": f.message}
                                 for f in result.findings],
                    "metrics": result.metrics,
                }
            except Exception as e:
                results[sc.check.name] = {"status": "ERROR", "error": str(e)}

        self._update_global_score()
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "global_score": self._global_score,
            "checks": results,
        }

    @property
    def status(self) -> dict[str, Any]:
        """Current status of all checks."""
        checks = {}
        for sc in self._checks:
            checks[sc.check.name] = {
                "status": sc.last_result.status if sc.last_result else "PENDING",
                "score": sc.last_result.score if sc.last_result else 100,
                "last_run": sc.last_run.isoformat() if sc.last_run else None,
                "run_count": sc.run_count,
                "error_count": sc.error_count,
                "interval_sec": sc.interval_sec,
                "findings": len(sc.last_result.findings) if sc.last_result else 0,
            }
        return {
            "global_score": self._global_score,
            "checks": checks,
        }

    @property
    def findings(self) -> list[dict]:
        """All current findings across all checks."""
        all_findings = []
        for sc in self._checks:
            if sc.last_result:
                for f in sc.last_result.findings:
                    all_findings.append({
                        "check": sc.check.name,
                        "severity": f.severity,
                        "code": f.code,
                        "message": f.message,
                    })
        return sorted(all_findings, key=lambda f: {"critical": 0, "error": 1, "warning": 2, "info": 3}.get(f["severity"], 9))
