"""Unit tests for the ESSScheduler."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models import (
    DeployTrigger,
    HealthCheckResult,
    HealthFinding,
    HealthSeverity,
)
from src.scheduler import ESSScheduler, MonitoringSession


def _make_deploy(window: int = 10, interval: int = 5) -> DeployTrigger:
    return DeployTrigger.model_validate(
        {
            "deployment": {
                "gitlab_pipeline_id": "1",
                "gitlab_project": "g/r",
                "commit_sha": "abc1234",
                "deployed_by": "test",
                "deployed_at": "2026-03-22T10:00:00Z",
                "environment": "production",
                "regions": ["ca"],
            },
            "services": [
                {
                    "name": "svc-a",
                    "datadog_service_name": "svc-a-dd",
                }
            ],
            "monitoring": {
                "window_minutes": window,
                "check_interval_minutes": interval,
            },
        }
    )


def _make_health_result(session: MonitoringSession) -> HealthCheckResult:
    return HealthCheckResult(
        job_id=session.job_id,
        cycle_number=session.checks_completed + 1,
        checked_at=datetime.now(tz=UTC),
        overall_severity=HealthSeverity.HEALTHY,
        findings=[
            HealthFinding(
                tool="stub",
                severity=HealthSeverity.HEALTHY,
                summary="all clear",
            )
        ],
        services_checked=[s.name for s in session.deploy.services],
    )


class TestSchedulerLifecycle:
    async def test_start_and_stop(self) -> None:
        sched = ESSScheduler()
        await sched.start()
        await sched.stop()

    async def test_schedule_returns_session(self) -> None:
        sched = ESSScheduler()
        await sched.start()
        try:
            deploy = _make_deploy()
            session = await sched.schedule_monitoring(
                job_id="ess-test01",
                deploy=deploy,
                health_check_fn=AsyncMock(side_effect=_make_health_result),
                on_complete_fn=AsyncMock(),
            )
            assert session.job_id == "ess-test01"
            assert session.status == "scheduled"
            assert session.checks_planned == 2  # window=10, interval=5
        finally:
            await sched.stop()

    async def test_max_sessions_enforced(self) -> None:
        sched = ESSScheduler(max_sessions=2)
        await sched.start()
        try:
            for i in range(2):
                await sched.schedule_monitoring(
                    job_id=f"ess-{i:02d}",
                    deploy=_make_deploy(),
                    health_check_fn=AsyncMock(side_effect=_make_health_result),
                    on_complete_fn=AsyncMock(),
                )
            with pytest.raises(ValueError, match="Maximum concurrent"):
                await sched.schedule_monitoring(
                    job_id="ess-overflow",
                    deploy=_make_deploy(),
                    health_check_fn=AsyncMock(side_effect=_make_health_result),
                    on_complete_fn=AsyncMock(),
                )
        finally:
            await sched.stop()


class TestCancellation:
    async def test_cancel_existing_job(self) -> None:
        sched = ESSScheduler()
        await sched.start()
        try:
            await sched.schedule_monitoring(
                job_id="ess-cancel01",
                deploy=_make_deploy(),
                health_check_fn=AsyncMock(side_effect=_make_health_result),
                on_complete_fn=AsyncMock(),
            )
            cancelled = await sched.cancel_monitoring("ess-cancel01")
            assert cancelled is True
            session = sched.get_session("ess-cancel01")
            assert session is not None
            assert session.status == "cancelled"
        finally:
            await sched.stop()

    async def test_cancel_nonexistent_job_returns_false(self) -> None:
        sched = ESSScheduler()
        await sched.start()
        try:
            result = await sched.cancel_monitoring("ess-ghost")
            assert result is False
        finally:
            await sched.stop()

    async def test_cancelled_session_not_in_active_list(self) -> None:
        sched = ESSScheduler()
        await sched.start()
        try:
            await sched.schedule_monitoring(
                job_id="ess-inactive",
                deploy=_make_deploy(),
                health_check_fn=AsyncMock(side_effect=_make_health_result),
                on_complete_fn=AsyncMock(),
            )
            await sched.cancel_monitoring("ess-inactive")
            active_ids = [s.job_id for s in sched.active_sessions()]
            assert "ess-inactive" not in active_ids
        finally:
            await sched.stop()


class TestAggregateSeverity:
    def test_no_results_returns_unknown(self) -> None:
        session = MagicMock(spec=MonitoringSession)
        session.results = []
        assert ESSScheduler._aggregate_severity(session) == HealthSeverity.UNKNOWN.value

    def test_all_healthy(self) -> None:
        session = MagicMock(spec=MonitoringSession)
        r1, r2 = MagicMock(), MagicMock()
        r1.overall_severity = HealthSeverity.HEALTHY
        r2.overall_severity = HealthSeverity.HEALTHY
        session.results = [r1, r2]
        assert ESSScheduler._aggregate_severity(session) == HealthSeverity.HEALTHY.value

    def test_mixed_escalates_to_worst(self) -> None:
        session = MagicMock(spec=MonitoringSession)
        r1, r2, r3 = MagicMock(), MagicMock(), MagicMock()
        r1.overall_severity = HealthSeverity.HEALTHY
        r2.overall_severity = HealthSeverity.WARNING
        r3.overall_severity = HealthSeverity.CRITICAL
        session.results = [r1, r2, r3]
        assert ESSScheduler._aggregate_severity(session) == HealthSeverity.CRITICAL.value


class TestRunCheckDirectly:
    """Tests that call _run_check directly to verify execution counting without
    waiting for real APScheduler ticks.
    """

    async def test_run_check_increments_checks_completed(self) -> None:
        sched = ESSScheduler()
        await sched.start()
        try:
            health_fn = AsyncMock(side_effect=_make_health_result)
            complete_fn = AsyncMock()
            deploy = _make_deploy(window=10, interval=5)
            session = await sched.schedule_monitoring(
                job_id="ess-runchk",
                deploy=deploy,
                health_check_fn=health_fn,
                on_complete_fn=complete_fn,
            )
            from datetime import timedelta

            end_time = session.started_at + timedelta(minutes=10)

            assert session.checks_completed == 0
            await sched._run_check("ess-runchk", health_fn, complete_fn, end_time)
            assert session.checks_completed == 1
            assert len(session.results) == 1
            health_fn.assert_awaited_once()
        finally:
            await sched.stop()

    async def test_run_check_health_fn_exception_does_not_crash_session(self) -> None:
        sched = ESSScheduler()
        await sched.start()
        try:
            failing_fn = AsyncMock(side_effect=RuntimeError("tool failure"))
            complete_fn = AsyncMock()
            deploy = _make_deploy(window=10, interval=5)
            session = await sched.schedule_monitoring(
                job_id="ess-failchk",
                deploy=deploy,
                health_check_fn=failing_fn,
                on_complete_fn=complete_fn,
            )
            from datetime import timedelta

            end_time = session.started_at + timedelta(minutes=10)

            await sched._run_check("ess-failchk", failing_fn, complete_fn, end_time)
            # Session should survive the failure
            assert session.last_error == "tool failure"
            assert session.checks_completed == 0  # count not incremented on failure
        finally:
            await sched.stop()

    async def test_run_check_triggers_completion_when_window_exhausted(self) -> None:
        sched = ESSScheduler()
        await sched.start()
        try:
            health_fn = AsyncMock(side_effect=_make_health_result)
            complete_fn = AsyncMock()
            deploy = _make_deploy(window=10, interval=5)
            session = await sched.schedule_monitoring(
                job_id="ess-complete01",
                deploy=deploy,
                health_check_fn=health_fn,
                on_complete_fn=complete_fn,
            )
            # Use an end_time in the past so the window is considered expired.
            from datetime import timedelta

            past_end = datetime.now(tz=UTC) - timedelta(minutes=1)

            await sched._run_check("ess-complete01", health_fn, complete_fn, past_end)
            # After completion the callback must have been called.
            complete_fn.assert_awaited_once()
            assert session.status == "completed"
        finally:
            await sched.stop()
