"""Tests for the Datadog Bedrock health-check agent."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent.health_check_agent import DatadogHealthCheckAgent
from src.agent.trace import AgentTraceRecorder
from src.models import DeployTrigger, HealthSeverity
from src.scheduler import MonitoringSession
from src.tools.pup_tool import PupResult
from src.tools.sentry_tool import (
    SentryIssue,
    SentryIssueDetail,
    SentryProjectDetails,
    SentryReleaseDetails,
    SentryResult,
)

_EXAMPLE_TRIGGER_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "examples"
    / "triggers"
    / "example-service-e2e.json"
)


class _FakeBedrockClient:
    def __init__(
        self,
        responses: list[dict] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._responses = responses or []
        self._error = error
        self.calls: list[dict] = []

    async def converse(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("No more fake Bedrock responses configured")
        return self._responses.pop(0)


def _load_example_trigger() -> DeployTrigger:
    return DeployTrigger.model_validate_json(_EXAMPLE_TRIGGER_PATH.read_text())


def _session() -> MonitoringSession:
    return MonitoringSession(
        job_id="ess-agent123",
        deploy=_load_example_trigger(),
        started_at=datetime.now(tz=UTC),
        checks_planned=12,
    )


def _ok_pup_result(data: dict | list | None = None) -> PupResult:
    return PupResult(
        command="pup test command",
        exit_code=0,
        data=data if data is not None else {"items": []},
        stderr="",
        duration_ms=15,
    )


def _mock_pup_tool() -> SimpleNamespace:
    return SimpleNamespace(
        get_monitor_status=AsyncMock(return_value=_ok_pup_result({"items": []})),
        search_error_logs=AsyncMock(return_value=_ok_pup_result({"items": []})),
        get_apm_stats=AsyncMock(return_value=_ok_pup_result({"summary": "stats ready"})),
        get_recent_incidents=AsyncMock(return_value=_ok_pup_result({"items": []})),
        get_infrastructure_health=AsyncMock(return_value=_ok_pup_result({"items": []})),
        get_apm_operations=AsyncMock(return_value=_ok_pup_result({"items": []})),
    )


def _ok_project_result() -> SentryResult[SentryProjectDetails]:
    return SentryResult(
        operation="get_project_details",
        request_path="/projects/example/example-service/",
        status_code=200,
        data=SentryProjectDetails.model_validate(
            {
                "id": 47,
                "slug": "example-service",
                "name": "Example Service",
                "platform": "java",
                "features": ["issue-stream"],
            }
        ),
        error=None,
        duration_ms=12,
        raw={"url": "https://sentry.example.com/api/0/projects/example/example-service/"},
    )


def _ok_release_result() -> SentryResult[SentryReleaseDetails]:
    return SentryResult(
        operation="get_release_details",
        request_path="/organizations/example/releases/2026.03.24-qa.1/",
        status_code=200,
        data=SentryReleaseDetails.model_validate(
            {
                "version": "2026.03.24-qa.1",
                "dateCreated": "2026-03-24T14:00:00Z",
                "lastEvent": "2026-03-24T14:10:00Z",
                "newGroups": 1,
                "projects": [{"id": 47, "slug": "example-service"}],
            }
        ),
        error=None,
        duration_ms=15,
        raw={
            "url": "https://sentry.example.com/api/0/organizations/example/releases/2026.03.24-qa.1/"
        },
    )


def _ok_new_release_issues() -> SentryResult[list[SentryIssue]]:
    return SentryResult(
        operation="query_new_release_issues",
        request_path="/organizations/example/issues/",
        status_code=200,
        data=[
            SentryIssue.model_validate(
                {
                    "id": "1001",
                    "title": "Regression after deploy",
                    "count": "4",
                    "userCount": "2",
                    "firstSeen": "2026-03-24T14:06:00Z",
                    "level": "error",
                }
            )
        ],
        error=None,
        duration_ms=18,
        raw={
            "params": {
                "query": (
                    'release:"2026.03.24-qa.1" firstSeen:>=2026-03-24T14:05:00Z '
                    "is:unresolved issue.category:error"
                )
            }
        },
    )


def _ok_issue_detail() -> SentryResult[SentryIssueDetail]:
    return SentryResult(
        operation="get_issue_details",
        request_path="/issues/1001/",
        status_code=200,
        data=SentryIssueDetail.model_validate(
            {
                "id": "1001",
                "title": "Regression after deploy",
                "count": "4",
                "userCount": "2",
                "latest_event": {"eventID": "evt-1", "message": "boom"},
            }
        ),
        error=None,
        duration_ms=21,
        raw={"issue": {}, "latest_event": {}},
    )


def _mock_sentry_tool() -> SimpleNamespace:
    return SimpleNamespace(
        get_project_details=AsyncMock(return_value=_ok_project_result()),
        get_release_details=AsyncMock(return_value=_ok_release_result()),
        query_new_release_issues=AsyncMock(return_value=_ok_new_release_issues()),
        get_issue_details=AsyncMock(return_value=_ok_issue_detail()),
    )


class TestDatadogHealthCheckAgent:
    @pytest.mark.asyncio
    async def test_runs_bedrock_tool_loop_and_returns_health_result(self) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        sentry_tool = _mock_sentry_tool()
        bedrock_client = _FakeBedrockClient(
            responses=[
                {
                    "output": {
                        "message": {
                            "content": [
                                {
                                    "toolUse": {
                                        "toolUseId": "tu-1",
                                        "name": "datadog_monitor_status",
                                        "input": {
                                            "service": "example-service",
                                            "environment": "qa",
                                        },
                                    }
                                },
                                {
                                    "toolUse": {
                                        "toolUseId": "tu-2",
                                        "name": "datadog_error_logs",
                                        "input": {
                                            "service": "example-service",
                                            "minutes_back": 10,
                                        },
                                    }
                                },
                            ]
                        }
                    },
                    "stopReason": "tool_use",
                },
                {
                    "output": {
                        "message": {
                            "content": [
                                {"text": "Severity: HEALTHY\nNo Datadog anomalies detected."}
                            ]
                        }
                    },
                    "stopReason": "end_turn",
                },
            ]
        )

        agent = DatadogHealthCheckAgent(
            bedrock_client=bedrock_client,
            pup_tool=pup_tool,
            sentry_tool=sentry_tool,
        )
        result = await agent.run_health_check(session)

        assert result.job_id == session.job_id
        assert result.cycle_number == 1
        assert result.overall_severity == HealthSeverity.HEALTHY
        assert result.services_checked == ["example-service"]
        assert any(finding.tool == "agent.summary" for finding in result.findings)
        assert any(finding.tool == "datadog.monitor_status" for finding in result.findings)
        assert "agent.summary" in result.raw_tool_outputs
        pup_tool.get_monitor_status.assert_awaited_once_with("example-service", "qa")
        pup_tool.search_error_logs.assert_awaited_once_with("example-service", minutes=10)
        sentry_tool.get_project_details.assert_not_awaited()
        sentry_tool.get_release_details.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runs_release_aware_sentry_follow_up_when_datadog_is_degraded(self) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        pup_tool.search_error_logs = AsyncMock(
            return_value=_ok_pup_result({"items": [{"message": "boom"}]})
        )
        sentry_tool = _mock_sentry_tool()
        bedrock_client = _FakeBedrockClient(
            responses=[
                {
                    "output": {
                        "message": {
                            "content": [
                                {
                                    "toolUse": {
                                        "toolUseId": "tu-1",
                                        "name": "datadog_error_logs",
                                        "input": {
                                            "service": "example-service",
                                            "minutes_back": 10,
                                        },
                                    }
                                }
                            ]
                        }
                    },
                    "stopReason": "tool_use",
                },
                {
                    "output": {
                        "message": {
                            "content": [
                                {
                                    "text": (
                                        "Severity: WARNING\n"
                                        "Recent Datadog errors detected after deploy."
                                    )
                                }
                            ]
                        }
                    },
                    "stopReason": "end_turn",
                },
            ]
        )

        agent = DatadogHealthCheckAgent(
            bedrock_client=bedrock_client,
            pup_tool=pup_tool,
            sentry_tool=sentry_tool,
        )
        result = await agent.run_health_check(session)

        assert result.overall_severity == HealthSeverity.WARNING
        assert any(finding.tool == "sentry.project_details" for finding in result.findings)
        assert any(finding.tool == "sentry.release_details" for finding in result.findings)
        assert any(finding.tool == "sentry.new_release_issues" for finding in result.findings)
        assert any(finding.tool == "sentry.issue_detail" for finding in result.findings)
        sentry_tool.get_project_details.assert_awaited_once_with("example-service")
        sentry_tool.get_release_details.assert_awaited_once_with("2026.03.24-qa.1")
        sentry_tool.query_new_release_issues.assert_awaited_once_with(
            47,
            "qa",
            "2026.03.24-qa.1",
            session.deploy.deployment.deployed_at,
            20,
        )
        sentry_tool.get_issue_details.assert_awaited_once_with("1001")

    @pytest.mark.asyncio
    async def test_falls_back_to_deterministic_triage_when_bedrock_fails(self) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        bedrock_client = _FakeBedrockClient(error=RuntimeError("bedrock unavailable"))

        agent = DatadogHealthCheckAgent(bedrock_client=bedrock_client, pup_tool=pup_tool)
        result = await agent.run_health_check(session)

        assert result.overall_severity == HealthSeverity.HEALTHY
        assert result.findings[0].tool == "agent.fallback"
        assert "bedrock unavailable" in (result.findings[0].details or "")
        assert "agent.fallback" in result.raw_tool_outputs
        pup_tool.get_monitor_status.assert_awaited_once_with("example-service", "qa")
        pup_tool.search_error_logs.assert_awaited_once_with("example-service")
        pup_tool.get_apm_stats.assert_awaited_once_with("example-service", "qa")

    @pytest.mark.asyncio
    async def test_falls_back_when_model_returns_no_tool_calls(self) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        bedrock_client = _FakeBedrockClient(
            responses=[
                {
                    "output": {"message": {"content": [{"text": "I think it looks healthy."}]}},
                    "stopReason": "end_turn",
                }
            ]
        )

        agent = DatadogHealthCheckAgent(bedrock_client=bedrock_client, pup_tool=pup_tool)
        result = await agent.run_health_check(session)

        assert result.findings[0].tool == "agent.fallback"
        assert "looks healthy" in (result.findings[0].details or "")

    @pytest.mark.asyncio
    async def test_writes_debug_trace_events_when_enabled(self, tmp_path) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        trace_path = tmp_path / "agent_trace.jsonl"
        trace_recorder = AgentTraceRecorder(enabled=True, path=trace_path)
        bedrock_client = _FakeBedrockClient(
            responses=[
                {
                    "output": {
                        "message": {
                            "content": [
                                {
                                    "toolUse": {
                                        "toolUseId": "tu-1",
                                        "name": "datadog_monitor_status",
                                        "input": {
                                            "service": "example-service",
                                            "environment": "qa",
                                        },
                                    }
                                }
                            ]
                        }
                    },
                    "stopReason": "tool_use",
                },
                {
                    "output": {
                        "message": {
                            "content": [
                                {"text": "Severity: HEALTHY\nNo Datadog anomalies detected."}
                            ]
                        }
                    },
                    "stopReason": "end_turn",
                },
            ]
        )

        agent = DatadogHealthCheckAgent(
            bedrock_client=bedrock_client,
            pup_tool=pup_tool,
            trace_recorder=trace_recorder,
        )

        await agent.run_health_check(session)

        session_trace_path = trace_recorder.path_for_trace(session.job_id)
        events = [json.loads(line) for line in session_trace_path.read_text().splitlines()]
        event_types = [event["event_type"] for event in events]

        assert "cycle.started" in event_types
        assert "bedrock.request" in event_types
        assert "bedrock.response" in event_types
        assert "tool.use" in event_types
        assert "tool.result" in event_types
        assert "cycle.completed" in event_types
