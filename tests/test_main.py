"""Unit tests for ESS FastAPI health-check and notification helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.agent.trace import AgentTraceRecorder
from src.config import ESSConfig
from src.main import (
    _build_completion_callback,
    _build_pup_health_check,
    _build_result_callback,
    _severity_from_tool_result,
)
from src.models import (
    DeployTrigger,
    HealthCheckResult,
    HealthFinding,
    HealthSeverity,
    ToolResult,
)
from src.notifications import TeamsDeliveryResult
from src.scheduler import MonitoringSession

_EXAMPLE_TRIGGER_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "examples"
    / "triggers"
    / "example-service-e2e.json"
)


class _FakeTeamsPublisher:
    def __init__(self, delivery: TeamsDeliveryResult | None = None) -> None:
        self._delivery = delivery or TeamsDeliveryResult(
            ok=True,
            status_code=200,
            response_text="1",
        )
        self.calls: list[tuple[str, dict]] = []

    async def post_card(self, webhook_url: str, card: dict) -> TeamsDeliveryResult:
        self.calls.append((webhook_url, card))
        return self._delivery


def _session() -> MonitoringSession:
    deploy = DeployTrigger.model_validate_json(_EXAMPLE_TRIGGER_PATH.read_text())
    return MonitoringSession(
        job_id="ess-test123",
        deploy=deploy,
        started_at=datetime.now(tz=UTC),
        checks_planned=2,
    )


def _tool_result(
    tool: str,
    *,
    success: bool = True,
    data: dict | None = None,
    summary: str = "ok",
    error: str | None = None,
) -> ToolResult:
    return ToolResult(
        tool=tool,
        success=success,
        data=data or {},
        summary=summary,
        error=error,
        duration_ms=5,
        raw={"command": "pup ..."},
    )


class TestSeverityFromToolResult:
    def test_error_logs_with_results_is_warning(self) -> None:
        result = _tool_result("datadog.error_logs", data={"items": [{"message": "boom"}]})
        assert _severity_from_tool_result(result) == HealthSeverity.WARNING

    def test_failed_tool_result_is_unknown(self) -> None:
        result = _tool_result(
            "datadog.monitor_status",
            success=False,
            summary="failed",
            error="timeout",
        )
        assert _severity_from_tool_result(result) == HealthSeverity.UNKNOWN


class TestBuildPupHealthCheck:
    async def test_builds_health_result_from_triage_calls(self) -> None:
        fake_tool = SimpleNamespace(
            get_monitor_status=AsyncMock(
                return_value=SimpleNamespace(
                    command="pup monitors list",
                    exit_code=0,
                    data={"items": []},
                    stderr="",
                    duration_ms=10,
                )
            ),
            search_error_logs=AsyncMock(
                return_value=SimpleNamespace(
                    command="pup logs search",
                    exit_code=0,
                    data={"items": []},
                    stderr="",
                    duration_ms=11,
                )
            ),
            get_apm_stats=AsyncMock(
                return_value=SimpleNamespace(
                    command="pup apm services stats",
                    exit_code=0,
                    data={"summary": "stats ready"},
                    stderr="",
                    duration_ms=12,
                )
            ),
        )

        health_check = _build_pup_health_check(fake_tool)
        result = await health_check(_session())

        assert result.job_id == "ess-test123"
        assert result.cycle_number == 1
        assert result.overall_severity == HealthSeverity.HEALTHY
        assert result.services_checked == ["example-service"]
        assert len(result.findings) == 3
        assert "example-service:datadog.monitor_status" in result.raw_tool_outputs

    async def test_alerting_monitor_escalates_to_critical(self) -> None:
        fake_tool = SimpleNamespace(
            get_monitor_status=AsyncMock(
                return_value=SimpleNamespace(
                    command="pup monitors list",
                    exit_code=0,
                    data={"items": [{"status": "Alert"}]},
                    stderr="",
                    duration_ms=10,
                )
            ),
            search_error_logs=AsyncMock(
                return_value=SimpleNamespace(
                    command="pup logs search",
                    exit_code=0,
                    data={"items": []},
                    stderr="",
                    duration_ms=11,
                )
            ),
            get_apm_stats=AsyncMock(
                return_value=SimpleNamespace(
                    command="pup apm services stats",
                    exit_code=0,
                    data={"summary": "stats ready"},
                    stderr="",
                    duration_ms=12,
                )
            ),
        )

        health_check = _build_pup_health_check(fake_tool)
        result = await health_check(_session())

        assert result.overall_severity == HealthSeverity.CRITICAL


class TestNotificationCallbacks:
    async def test_result_callback_skips_when_teams_disabled(self, tmp_path) -> None:
        session = _session()
        result = HealthCheckResult(
            job_id=session.job_id,
            cycle_number=1,
            checked_at=datetime.now(tz=UTC),
            overall_severity=HealthSeverity.CRITICAL,
            findings=[
                HealthFinding(
                    tool="datadog.monitor_status",
                    severity=HealthSeverity.CRITICAL,
                    summary="critical monitor",
                )
            ],
            services_checked=["example-service"],
        )
        session.checks_completed = 1
        session.results.append(result)
        trace_path = tmp_path / "agent_trace.jsonl"
        recorder = AgentTraceRecorder(enabled=True, path=trace_path)
        callback = _build_result_callback(
            ESSConfig(
                _env_file=None,
                dd_api_key="k",
                dd_app_key="a",
                sentry_auth_token="s",
                teams_enabled=False,
            ),
            recorder,
            _FakeTeamsPublisher(),
        )

        await callback(session, result)

        session_trace_path = recorder.path_for_trace(session.job_id)
        events = [json.loads(line) for line in session_trace_path.read_text().splitlines()]
        assert events[-1]["event_type"] == "notification.skipped"
        assert events[-1]["attributes"]["reason"] == "teams_disabled"

    async def test_result_callback_posts_repeated_warning(self, tmp_path) -> None:
        session = _session()
        first = HealthCheckResult(
            job_id=session.job_id,
            cycle_number=1,
            checked_at=datetime.now(tz=UTC),
            overall_severity=HealthSeverity.WARNING,
            findings=[
                HealthFinding(
                    tool="datadog.error_logs",
                    severity=HealthSeverity.WARNING,
                    summary="warning one",
                )
            ],
            services_checked=["example-service"],
        )
        second = HealthCheckResult(
            job_id=session.job_id,
            cycle_number=2,
            checked_at=datetime.now(tz=UTC),
            overall_severity=HealthSeverity.WARNING,
            findings=[
                HealthFinding(
                    tool="datadog.error_logs",
                    severity=HealthSeverity.WARNING,
                    summary="warning two",
                )
            ],
            services_checked=["example-service"],
        )
        session.checks_completed = 2
        session.results.extend([first, second])
        trace_path = tmp_path / "agent_trace.jsonl"
        recorder = AgentTraceRecorder(enabled=True, path=trace_path)
        publisher = _FakeTeamsPublisher()
        callback = _build_result_callback(
            ESSConfig(
                _env_file=None,
                dd_api_key="k",
                dd_app_key="a",
                sentry_auth_token="s",
                teams_enabled=True,
                default_teams_webhook_url="https://outlook.office.com/webhook/test",
            ),
            recorder,
            publisher,
        )

        await callback(session, second)

        session_trace_path = recorder.path_for_trace(session.job_id)
        events = [json.loads(line) for line in session_trace_path.read_text().splitlines()]
        assert publisher.calls
        assert events[-2]["event_type"] == "notification.attempted"
        assert events[-1]["event_type"] == "notification.delivered"

    async def test_completion_callback_posts_summary(self, tmp_path) -> None:
        session = _session()
        result = HealthCheckResult(
            job_id=session.job_id,
            cycle_number=1,
            checked_at=datetime.now(tz=UTC),
            overall_severity=HealthSeverity.HEALTHY,
            findings=[
                HealthFinding(
                    tool="datadog.apm_stats",
                    severity=HealthSeverity.HEALTHY,
                    summary="all clear",
                )
            ],
            services_checked=["example-service"],
        )
        session.checks_completed = 1
        session.results.append(result)
        trace_path = tmp_path / "agent_trace.jsonl"
        recorder = AgentTraceRecorder(enabled=True, path=trace_path)
        publisher = _FakeTeamsPublisher()
        callback = _build_completion_callback(
            ESSConfig(
                _env_file=None,
                dd_api_key="k",
                dd_app_key="a",
                sentry_auth_token="s",
                teams_enabled=True,
                default_teams_webhook_url="https://outlook.office.com/webhook/test",
            ),
            recorder,
            publisher,
        )

        await callback(session)

        session_trace_path = recorder.path_for_trace(session.job_id)
        events = [json.loads(line) for line in session_trace_path.read_text().splitlines()]
        assert publisher.calls
        assert events[0]["event_type"] == "session.completed"
        assert events[-1]["event_type"] == "notification.delivered"
