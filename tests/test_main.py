"""Unit tests for the ESS FastAPI module's health-check helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.main import _build_pup_health_check, _severity_from_tool_result
from src.models import HealthSeverity, ToolResult
from src.scheduler import MonitoringSession


def _session() -> MonitoringSession:
    deploy = SimpleNamespace(
        deployment=SimpleNamespace(environment=SimpleNamespace(value="qa")),
        services=[
            SimpleNamespace(name="well-logs", datadog_service_name="example-well-service"),
        ],
    )
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
        assert result.services_checked == ["well-logs"]
        assert len(result.findings) == 3
        assert "well-logs:datadog.monitor_status" in result.raw_tool_outputs

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
