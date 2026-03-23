"""ESS job scheduler — APScheduler AsyncIOScheduler wrapper.

Each deploy trigger creates an interval job that:
1. Fires every ``check_interval_minutes`` for the full monitoring window
2. Calls the health-check callback on each tick
3. Auto-removes itself after ``window_minutes``
4. Posts a final summary notification on completion

Jobs are stored in memory for v1.  Session state is held in the
``MonitoringSession`` dataclass alongside the APScheduler job.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.models import DeployTrigger, HealthCheckResult, HealthSeverity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class MonitoringSession:
    """Tracks the full lifecycle of one monitoring window."""

    job_id: str
    deploy: DeployTrigger
    started_at: datetime
    checks_completed: int = 0
    checks_planned: int = 0
    results: list[HealthCheckResult] = field(default_factory=list)
    status: str = "scheduled"  # scheduled | running | completed | cancelled | error
    next_check_at: datetime | None = None
    last_error: str | None = None


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

# Type alias for the health-check callback injected by the FastAPI app layer.
HealthCheckFn = Callable[[MonitoringSession], Awaitable[HealthCheckResult]]
CompletionFn = Callable[[MonitoringSession], Awaitable[None]]


class ESSScheduler:
    """Thin wrapper around APScheduler's AsyncIOScheduler.

    Lifecycle:
        scheduler = ESSScheduler(max_sessions=20)
        await scheduler.start()
        job_id = await scheduler.schedule_monitoring(deploy, health_check_fn, on_complete_fn)
        await scheduler.cancel_monitoring(job_id)
        await scheduler.stop()
    """

    def __init__(self, max_sessions: int = 20) -> None:
        self._scheduler = AsyncIOScheduler()
        self._sessions: dict[str, MonitoringSession] = {}
        self._max_sessions = max_sessions
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._scheduler.start()
        logger.info("ESS scheduler started")

    async def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("ESS scheduler stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def schedule_monitoring(
        self,
        job_id: str,
        deploy: DeployTrigger,
        health_check_fn: HealthCheckFn,
        on_complete_fn: CompletionFn,
    ) -> MonitoringSession:
        """Create a new interval job for this deployment.

        Returns the ``MonitoringSession`` immediately; the first health-check
        fires after ``check_interval_minutes``.

        Raises ``ValueError`` if the maximum number of concurrent sessions is
        already reached.
        """
        async with self._lock:
            if len(self._sessions) >= self._max_sessions:
                raise ValueError(
                    f"Maximum concurrent monitoring sessions ({self._max_sessions}) reached"
                )

            window = deploy.monitoring.window_minutes
            interval = deploy.monitoring.check_interval_minutes
            checks_planned = max(1, window // interval)

            now = datetime.now(tz=UTC)
            session = MonitoringSession(
                job_id=job_id,
                deploy=deploy,
                started_at=now,
                checks_planned=checks_planned,
                status="scheduled",
                next_check_at=now + timedelta(minutes=interval),
            )
            self._sessions[job_id] = session

        # Schedule the interval job.  APScheduler will call _run_check on every
        # tick until _cancel_after fires at the end of the window.
        end_time = session.started_at + timedelta(minutes=window)

        self._scheduler.add_job(
            self._run_check,
            trigger=IntervalTrigger(minutes=interval),
            id=job_id,
            end_date=end_time,
            args=[job_id, health_check_fn, on_complete_fn, end_time],
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )

        logger.info(
            "Monitoring session scheduled",
            extra={
                "job_id": job_id,
                "services": [s.name for s in deploy.services],
                "window_minutes": window,
                "check_interval_minutes": interval,
                "checks_planned": checks_planned,
            },
        )
        return session

    async def cancel_monitoring(self, job_id: str) -> bool:
        """Cancel an active monitoring session.

        Returns ``True`` if cancelled, ``False`` if not found.
        """
        async with self._lock:
            session = self._sessions.get(job_id)
            if session is None:
                return False
            session.status = "cancelled"

        with contextlib.suppress(Exception):
            self._scheduler.remove_job(job_id)

        logger.info("Monitoring session cancelled", extra={"job_id": job_id})
        return True

    def get_session(self, job_id: str) -> MonitoringSession | None:
        return self._sessions.get(job_id)

    def active_sessions(self) -> list[MonitoringSession]:
        return [s for s in self._sessions.values() if s.status in ("scheduled", "running")]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_check(
        self,
        job_id: str,
        health_check_fn: HealthCheckFn,
        on_complete_fn: CompletionFn,
        end_time: datetime,
    ) -> None:
        session = self._sessions.get(job_id)
        if session is None or session.status in ("cancelled", "completed", "error"):
            return

        session.status = "running"
        cycle = session.checks_completed + 1

        try:
            result = await health_check_fn(session)
            async with self._lock:
                session.checks_completed += 1
                session.results.append(result)
                interval = session.deploy.monitoring.check_interval_minutes
                now = datetime.now(tz=UTC)
                next_at = now + timedelta(minutes=interval)
                session.next_check_at = next_at if next_at < end_time else None
                session.status = "running"
        except Exception as exc:
            logger.exception(
                "Health check failed",
                extra={"job_id": job_id, "cycle": cycle, "error": str(exc)},
            )
            async with self._lock:
                session.last_error = str(exc)
            # Do not mark session as error — continue monitoring window.

        # Check whether the window has closed (APScheduler fires end_date
        # inclusive; we also handle the case where checks_completed reached
        # checks_planned first).
        now = datetime.now(tz=UTC)
        if now >= end_time or session.checks_completed >= session.checks_planned:
            await self._complete_session(job_id, on_complete_fn)

    async def _complete_session(
        self,
        job_id: str,
        on_complete_fn: CompletionFn,
    ) -> None:
        async with self._lock:
            session = self._sessions.get(job_id)
            if session is None or session.status in ("completed", "cancelled"):
                return
            session.status = "completed"
            session.next_check_at = None

        with contextlib.suppress(Exception):
            self._scheduler.remove_job(job_id)

        try:
            await on_complete_fn(session)
        except Exception as exc:
            logger.exception(
                "Completion callback failed",
                extra={"job_id": job_id, "error": str(exc)},
            )

        logger.info(
            "Monitoring session completed",
            extra={
                "job_id": job_id,
                "checks_completed": session.checks_completed,
                "overall_severity": self._aggregate_severity(session),
            },
        )

    @staticmethod
    def _aggregate_severity(session: MonitoringSession) -> str:
        """Return the worst severity seen across all check results."""
        if not session.results:
            return HealthSeverity.UNKNOWN.value
        order = [HealthSeverity.HEALTHY, HealthSeverity.WARNING, HealthSeverity.CRITICAL]
        worst = HealthSeverity.HEALTHY
        for result in session.results:
            if order.index(result.overall_severity) > order.index(worst):
                worst = result.overall_severity
        return worst.value
