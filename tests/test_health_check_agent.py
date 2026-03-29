"""Tests for the Phase 3 Datadog + Sentry health-check agent."""

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
        model_id: str = "fake-model",
    ) -> None:
        self._responses = responses or []
        self._error = error
        self._model_id = model_id
        self.calls: list[dict] = []

    async def converse(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("No more fake Bedrock responses configured")
        return self._responses.pop(0)

    @property
    def model_id(self) -> str:
        return self._model_id


def _load_example_trigger() -> DeployTrigger:
    return DeployTrigger.model_validate_json(_EXAMPLE_TRIGGER_PATH.read_text())


def _session() -> MonitoringSession:
    return MonitoringSession(
        job_id="ess-agent123",
        deploy=_load_example_trigger(),
        started_at=datetime.now(tz=UTC),
        checks_planned=12,
    )


def _text_response(text: str) -> dict:
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "stopReason": "end_turn",
    }


def _tool_use_response(*tool_uses: dict) -> dict:
    return {
        "output": {"message": {"content": [{"toolUse": tool_use} for tool_use in tool_uses]}},
        "stopReason": "tool_use",
    }


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
        get_apm_operations=AsyncMock(return_value=_ok_pup_result({"operations": []})),
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
    async def test_runs_triage_only_when_datadog_stays_healthy(self) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        sentry_tool = _mock_sentry_tool()
        triage_client = _FakeBedrockClient(
            responses=[
                _tool_use_response(
                    {
                        "toolUseId": "tu-1",
                        "name": "datadog_monitor_status",
                        "input": {"service": "example-service", "environment": "qa"},
                    },
                    {
                        "toolUseId": "tu-2",
                        "name": "datadog_error_logs",
                        "input": {"service": "example-service", "minutes_back": 10},
                    },
                ),
                _text_response("Severity: HEALTHY\nNo Datadog anomalies detected."),
            ],
            model_id="triage-model",
        )
        investigation_client = _FakeBedrockClient(model_id="investigation-model")

        agent = DatadogHealthCheckAgent(
            bedrock_client=triage_client,
            investigation_client=investigation_client,
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
        assert not any(finding.tool.startswith("sentry.") for finding in result.findings)
        assert investigation_client.calls == []
        pup_tool.get_monitor_status.assert_awaited_once_with("example-service", "qa")
        pup_tool.search_error_logs.assert_awaited_once_with("example-service", minutes=10)
        sentry_tool.get_project_details.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runs_bedrock_investigation_for_degraded_service(self) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        pup_tool.search_error_logs = AsyncMock(
            return_value=_ok_pup_result({"items": [{"message": "boom"}]})
        )
        pup_tool.get_apm_operations = AsyncMock(
            return_value=_ok_pup_result({"operations": [{"name": "POST /auth", "errors": 4}]})
        )
        sentry_tool = _mock_sentry_tool()
        triage_client = _FakeBedrockClient(
            responses=[
                _tool_use_response(
                    {
                        "toolUseId": "tu-1",
                        "name": "datadog_error_logs",
                        "input": {"service": "example-service", "minutes_back": 10},
                    }
                ),
                _text_response(
                    "Severity: WARNING\nRecent Datadog errors detected after deploy."
                ),
            ],
            model_id="triage-model",
        )
        investigation_client = _FakeBedrockClient(
            responses=[
                _tool_use_response(
                    {
                        "toolUseId": "tu-3",
                        "name": "sentry_project_details",
                        "input": {"project_slug": "example-service"},
                    },
                    {
                        "toolUseId": "tu-4",
                        "name": "sentry_release_details",
                        "input": {"release_version": "2026.03.24-qa.1"},
                    },
                    {
                        "toolUseId": "tu-5",
                        "name": "sentry_new_release_issues",
                        "input": {
                            "project": 47,
                            "environment": "qa",
                            "release_version": "2026.03.24-qa.1",
                            "effective_since": "2026-03-24T14:05:00+00:00",
                            "per_page": 20,
                        },
                    },
                    {
                        "toolUseId": "tu-6",
                        "name": "datadog_apm_operations",
                        "input": {"service": "example-service", "environment": "qa"},
                    },
                ),
                _text_response(
                    "Severity: WARNING\n"
                    "Investigation correlated new Sentry errors with the degraded route."
                ),
            ],
            model_id="investigation-model",
        )

        agent = DatadogHealthCheckAgent(
            bedrock_client=triage_client,
            investigation_client=investigation_client,
            pup_tool=pup_tool,
            sentry_tool=sentry_tool,
        )
        result = await agent.run_health_check(session)

        assert result.overall_severity == HealthSeverity.WARNING
        assert any(finding.tool == "agent.investigation_summary" for finding in result.findings)
        assert any(finding.tool == "sentry.project_details" for finding in result.findings)
        assert any(finding.tool == "sentry.release_details" for finding in result.findings)
        assert any(finding.tool == "sentry.new_release_issues" for finding in result.findings)
        assert any(finding.tool == "datadog.apm_operations" for finding in result.findings)
        sentry_tool.get_project_details.assert_awaited_once_with("example-service")
        sentry_tool.get_release_details.assert_awaited_once_with("2026.03.24-qa.1")
        sentry_tool.query_new_release_issues.assert_awaited_once_with(
            47,
            "qa",
            "2026.03.24-qa.1",
            session.deploy.deployment.deployed_at,
            20,
        )
        pup_tool.get_apm_operations.assert_awaited_once_with("example-service", "qa")

    @pytest.mark.asyncio
    async def test_investigation_failure_falls_back_to_deterministic_sentry_follow_up(self) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        pup_tool.search_error_logs = AsyncMock(
            return_value=_ok_pup_result({"items": [{"message": "boom"}]})
        )
        sentry_tool = _mock_sentry_tool()
        triage_client = _FakeBedrockClient(
            responses=[
                _tool_use_response(
                    {
                        "toolUseId": "tu-1",
                        "name": "datadog_error_logs",
                        "input": {"service": "example-service", "minutes_back": 10},
                    }
                ),
                _text_response(
                    "Severity: WARNING\nRecent Datadog errors detected after deploy."
                ),
            ]
        )
        investigation_client = _FakeBedrockClient(error=RuntimeError("investigation unavailable"))

        agent = DatadogHealthCheckAgent(
            bedrock_client=triage_client,
            investigation_client=investigation_client,
            pup_tool=pup_tool,
            sentry_tool=sentry_tool,
        )
        result = await agent.run_health_check(session)

        assert result.overall_severity == HealthSeverity.WARNING
        assert any(finding.tool == "sentry.project_details" for finding in result.findings)
        assert any(finding.tool == "sentry.release_details" for finding in result.findings)
        assert any(finding.tool == "sentry.new_release_issues" for finding in result.findings)
        sentry_tool.get_project_details.assert_awaited_once_with("example-service")
        sentry_tool.get_release_details.assert_awaited_once_with("2026.03.24-qa.1")
        sentry_tool.query_new_release_issues.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_deterministic_triage_when_bedrock_fails(self) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        triage_client = _FakeBedrockClient(error=RuntimeError("bedrock unavailable"))

        agent = DatadogHealthCheckAgent(bedrock_client=triage_client, pup_tool=pup_tool)
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
        triage_client = _FakeBedrockClient(
            responses=[_text_response("I think it looks healthy.")]
        )

        agent = DatadogHealthCheckAgent(bedrock_client=triage_client, pup_tool=pup_tool)
        result = await agent.run_health_check(session)

        assert result.findings[0].tool == "agent.fallback"
        assert "looks healthy" in (result.findings[0].details or "")

    @pytest.mark.asyncio
    async def test_compacts_conversation_when_token_budget_is_exceeded(self, tmp_path) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        trace_path = tmp_path / "agent_trace.jsonl"
        trace_recorder = AgentTraceRecorder(enabled=True, path=trace_path)
        triage_client = _FakeBedrockClient(
            responses=[
                _tool_use_response(
                    {
                        "toolUseId": "tu-1",
                        "name": "datadog_monitor_status",
                        "input": {"service": "example-service", "environment": "qa"},
                    }
                ),
                _tool_use_response(
                    {
                        "toolUseId": "tu-2",
                        "name": "datadog_error_logs",
                        "input": {"service": "example-service", "minutes_back": 10},
                    }
                ),
                _text_response("Previous evidence compacted into a short summary."),
                _text_response("Severity: HEALTHY\nNo Datadog anomalies detected."),
            ]
        )

        agent = DatadogHealthCheckAgent(
            bedrock_client=triage_client,
            pup_tool=pup_tool,
            max_tokens_budget=10,
            trace_recorder=trace_recorder,
        )

        await agent.run_health_check(session)

        assert len(triage_client.calls) == 4
        assert "tool_config" in triage_client.calls[0]
        assert "tool_config" in triage_client.calls[1]
        assert "tool_config" not in triage_client.calls[2]

        session_trace_path = trace_recorder.path_for_trace(session.job_id)
        events = [json.loads(line) for line in session_trace_path.read_text().splitlines()]
        event_types = [event["event_type"] for event in events]
        assert "conversation.compacted" in event_types

    @pytest.mark.asyncio
    async def test_writes_debug_trace_events_when_enabled(self, tmp_path) -> None:
        session = _session()
        pup_tool = _mock_pup_tool()
        trace_path = tmp_path / "agent_trace.jsonl"
        trace_recorder = AgentTraceRecorder(enabled=True, path=trace_path)
        triage_client = _FakeBedrockClient(
            responses=[
                _tool_use_response(
                    {
                        "toolUseId": "tu-1",
                        "name": "datadog_monitor_status",
                        "input": {"service": "example-service", "environment": "qa"},
                    }
                ),
                _text_response("Severity: HEALTHY\nNo Datadog anomalies detected."),
            ]
        )

        agent = DatadogHealthCheckAgent(
            bedrock_client=triage_client,
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
