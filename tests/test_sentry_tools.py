"""Tests for Sentry Bedrock tool definitions and dispatch helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent.sentry_tools import (
    SENTRY_TOOL_CONFIG,
    build_sentry_tool_prompt_fragment,
    dispatch_sentry_tool_call,
    execute_sentry_tool_use,
    execute_sentry_tool_uses,
    sentry_tool_result_payload,
)
from src.llm_client import BedrockClient, build_assistant_message, build_user_message
from src.models import DeployTrigger, ServiceTarget
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


def _effective_since() -> datetime:
    return datetime(2026, 3, 24, 14, 5, tzinfo=UTC)


def _ok_project_result() -> SentryResult[SentryProjectDetails]:
    return SentryResult(
        operation="get_project_details",
        request_path="/projects/example-org/auth-service/",
        status_code=200,
        data=SentryProjectDetails.model_validate(
            {
                "id": "47",
                "slug": "auth-service",
                "name": "Auth Service",
                "platform": "python",
                "features": ["issue-stream", "performance-view"],
            }
        ),
        error=None,
        duration_ms=12,
        raw={"url": "https://sentry.example.com/api/0/projects/example-org/auth-service/"},
    )


def _ok_release_result() -> SentryResult[SentryReleaseDetails]:
    return SentryResult(
        operation="get_release_details",
        request_path="/organizations/example-org/releases/2026.03.24-qa.1/",
        status_code=200,
        data=SentryReleaseDetails.model_validate(
            {
                "version": "2026.03.24-qa.1",
                "dateCreated": "2026-03-24T14:00:00Z",
                "lastEvent": "2026-03-24T14:08:00Z",
                "newGroups": 2,
                "projects": [
                    {
                        "id": "47",
                        "slug": "auth-service",
                        "name": "Auth Service",
                        "platform": "python",
                        "hasHealthData": False,
                    }
                ],
            }
        ),
        error=None,
        duration_ms=15,
        raw={
            "url": "https://sentry.example.com/api/0/organizations/example-org/releases/2026.03.24-qa.1/"
        },
    )


def _ok_issue_result() -> SentryResult[list[SentryIssue]]:
    return SentryResult(
        operation="query_new_release_issues",
        request_path="/organizations/example-org/issues/",
        status_code=200,
        data=[
            SentryIssue.model_validate(
                {
                    "id": "1001",
                    "title": "TypeError in auth flow",
                    "count": "7",
                    "userCount": "3",
                    "level": "error",
                }
            )
        ],
        error=None,
        duration_ms=22,
        raw={
            "params": {
                "query": (
                    'release:"2026.03.24-qa.1" firstSeen:>=2026-03-24T14:05:00Z '
                    "is:unresolved issue.category:error"
                )
            }
        },
    )


def _ok_detail_result() -> SentryResult[SentryIssueDetail]:
    return SentryResult(
        operation="get_issue_details",
        request_path="/issues/1001",
        status_code=200,
        data=SentryIssueDetail.model_validate(
            {
                "id": "1001",
                "title": "TypeError in auth flow",
                "count": "7",
                "userCount": "3",
                "latest_event": {"eventID": "evt-1", "message": "boom"},
            }
        ),
        error=None,
        duration_ms=18,
        raw={"issue": {}, "latest_event": {}},
    )


def _mock_sentry_tool() -> SimpleNamespace:
    return SimpleNamespace(
        get_project_details=AsyncMock(return_value=_ok_project_result()),
        get_release_details=AsyncMock(return_value=_ok_release_result()),
        query_new_release_issues=AsyncMock(return_value=_ok_issue_result()),
        get_issue_details=AsyncMock(return_value=_ok_detail_result()),
    )


def _load_example_trigger() -> DeployTrigger:
    return DeployTrigger.model_validate_json(_EXAMPLE_TRIGGER_PATH.read_text())


def _contains_schema_ref(value: object) -> bool:
    if isinstance(value, dict):
        if "$ref" in value or "$defs" in value:
            return True
        return any(_contains_schema_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_schema_ref(item) for item in value)
    return False


class TestSentryToolConfig:
    def test_exports_bedrock_tool_config_with_expected_names(self) -> None:
        tool_specs = SENTRY_TOOL_CONFIG["tools"]
        tool_names = {tool["toolSpec"]["name"] for tool in tool_specs}

        assert tool_names == {
            "sentry_project_details",
            "sentry_release_details",
            "sentry_new_release_issues",
            "sentry_issue_details",
        }
        assert "sentry_search_traces" not in tool_names

    def test_new_release_issues_schema_uses_effective_since_and_per_page_defaults(self) -> None:
        query_spec = next(
            tool["toolSpec"]
            for tool in SENTRY_TOOL_CONFIG["tools"]
            if tool["toolSpec"]["name"] == "sentry_new_release_issues"
        )
        schema = query_spec["inputSchema"]["json"]

        assert schema["required"] == [
            "project",
            "environment",
            "release_version",
            "effective_since",
        ]
        assert schema["properties"]["per_page"]["default"] == 20

    def test_tool_schemas_are_self_contained_for_bedrock(self) -> None:
        assert _contains_schema_ref(SENTRY_TOOL_CONFIG) is False


class TestBuildSentryToolPromptFragment:
    def test_appends_service_name_mapping(self) -> None:
        fragment = build_sentry_tool_prompt_fragment(
            [
                ServiceTarget(
                    name="hub-ca-auth",
                    datadog_service_name="example-auth-service",
                    sentry_project="auth-service",
                    sentry_project_id=47,
                )
            ]
        )

        assert "sentry_new_release_issues" in fragment
        assert "release_version" in fragment
        assert "sentry_project_id" in fragment
        assert "Datadog is the first signal" in fragment
        assert "hub-ca-auth" in fragment
        assert "auth-service" in fragment
        assert "47" in fragment

    def test_uses_real_example_trigger_service_mapping(self) -> None:
        trigger = _load_example_trigger()

        fragment = build_sentry_tool_prompt_fragment(trigger.services)

        assert "sentry_project_details" in fragment
        assert "Sentry project mappings for this deployment" in fragment
        assert "example-service" in fragment
        assert "project id 47" in fragment


class TestDispatchSentryToolCall:
    @pytest.mark.asyncio
    async def test_project_details_dispatches_and_normalises_output(self) -> None:
        sentry_tool = _mock_sentry_tool()

        result = await dispatch_sentry_tool_call(
            sentry_tool,
            "sentry_project_details",
            {"project_slug": "auth-service"},
        )

        sentry_tool.get_project_details.assert_awaited_once_with("auth-service")
        assert result.success is True
        assert result.tool == "sentry.project_details"
        assert result.data["slug"] == "auth-service"

    @pytest.mark.asyncio
    async def test_new_release_issues_dispatches_and_normalises_output(self) -> None:
        sentry_tool = _mock_sentry_tool()
        effective_since = _effective_since()

        result = await dispatch_sentry_tool_call(
            sentry_tool,
            "sentry_new_release_issues",
            {
                "project": 47,
                "environment": "qa",
                "release_version": "2026.03.24-qa.1",
                "effective_since": effective_since.isoformat(),
                "per_page": 15,
            },
        )

        sentry_tool.query_new_release_issues.assert_awaited_once_with(
            47,
            "qa",
            "2026.03.24-qa.1",
            effective_since,
            15,
        )
        assert result.success is True
        assert result.tool == "sentry.new_release_issues"
        assert result.data["items"][0]["title"] == "TypeError in auth flow"

    @pytest.mark.asyncio
    async def test_issue_detail_dispatches_and_preserves_latest_event(self) -> None:
        sentry_tool = _mock_sentry_tool()

        result = await dispatch_sentry_tool_call(
            sentry_tool,
            "sentry_issue_details",
            {"issue_id": "1001"},
        )

        sentry_tool.get_issue_details.assert_awaited_once_with("1001")
        assert result.success is True
        assert result.tool == "sentry.issue_detail"
        assert result.data["latest_event"]["eventID"] == "evt-1"

    @pytest.mark.asyncio
    async def test_invalid_new_release_issues_input_returns_error(self) -> None:
        sentry_tool = _mock_sentry_tool()

        result = await dispatch_sentry_tool_call(
            sentry_tool,
            "sentry_new_release_issues",
            {
                "project": 47,
                "environment": "qa",
                "release_version": "2026.03.24-qa.1",
                "effective_since": _effective_since().isoformat(),
                "per_page": 0,
            },
        )

        sentry_tool.query_new_release_issues.assert_not_awaited()
        assert result.success is False
        assert result.tool == "sentry.new_release_issues"
        assert "per_page" in (result.error or "")

    @pytest.mark.asyncio
    async def test_unknown_tool_name_returns_error_result(self) -> None:
        sentry_tool = _mock_sentry_tool()

        result = await dispatch_sentry_tool_call(
            sentry_tool,
            "sentry_not_real",
            {"project": "auth-service"},
        )

        assert result.success is False
        assert result.tool == "sentry.not_real"
        assert "Unknown Sentry tool requested" in result.summary


class TestSentryToolResultPayload:
    def test_preserves_error_details_when_present(self) -> None:
        payload = sentry_tool_result_payload(
            result=SimpleNamespace(
                tool="sentry.new_release_issues",
                success=False,
                summary="query failed",
                data={},
                duration_ms=0,
                error="timeout",
                raw={"input": {"project": "svc"}},
            )
        )

        assert payload["error"] == "timeout"
        assert payload["raw"] == {"input": {"project": "svc"}}


class TestExecuteSentryToolUse:
    @pytest.mark.asyncio
    async def test_builds_tool_result_message(self) -> None:
        sentry_tool = _mock_sentry_tool()

        result, message = await execute_sentry_tool_use(
            sentry_tool,
            {
                "toolUseId": "tu-001",
                "name": "sentry_release_details",
                "input": {"release_version": "2026.03.24-qa.1"},
            },
        )

        tool_result = message["content"][0]["toolResult"]
        payload = json.loads(tool_result["content"][0]["text"])

        assert result.tool == "sentry.release_details"
        assert tool_result["toolUseId"] == "tu-001"
        assert "status" not in tool_result
        assert payload["tool"] == "sentry.release_details"
        assert payload["success"] is True

    @pytest.mark.asyncio
    async def test_invalid_tool_input_marks_message_as_error(self) -> None:
        sentry_tool = _mock_sentry_tool()

        result, message = await execute_sentry_tool_use(
            sentry_tool,
            {
                "toolUseId": "tu-002",
                "name": "sentry_issue_details",
                "input": {},
            },
        )

        assert result.success is False
        assert message["content"][0]["toolResult"]["status"] == "error"


class TestExecuteSentryToolUses:
    @pytest.mark.asyncio
    async def test_mock_llm_tool_use_round_trip(self) -> None:
        sentry_tool = _mock_sentry_tool()
        assistant_response = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tu-100",
                                "name": "sentry_project_details",
                                "input": {"project_slug": "auth-service"},
                            }
                        },
                        {
                            "toolUse": {
                                "toolUseId": "tu-101",
                                "name": "sentry_new_release_issues",
                                "input": {
                                    "project": 47,
                                    "environment": "qa",
                                    "release_version": "2026.03.24-qa.1",
                                    "effective_since": _effective_since().isoformat(),
                                },
                            }
                        },
                    ]
                }
            },
            "stopReason": "tool_use",
        }

        tool_uses = BedrockClient.extract_tool_uses(assistant_response)
        results, tool_messages = await execute_sentry_tool_uses(sentry_tool, tool_uses)
        conversation = [
            build_user_message("check the deployment"),
            build_assistant_message(assistant_response),
            *tool_messages,
        ]

        assert len(results) == 2
        assert results[0].tool == "sentry.project_details"
        assert results[1].tool == "sentry.new_release_issues"
        assert conversation[1]["role"] == "assistant"
        assert conversation[2]["content"][0]["toolResult"]["toolUseId"] == "tu-100"
        assert conversation[2]["content"][1]["toolResult"]["toolUseId"] == "tu-101"

    @pytest.mark.asyncio
    async def test_mock_llm_round_trip_with_real_trigger_example(self) -> None:
        trigger = _load_example_trigger()
        sentry_tool = _mock_sentry_tool()
        service = trigger.services[0]

        assistant_response = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tu-200",
                                "name": "sentry_project_details",
                                "input": {
                                    "project_slug": service.sentry_project,
                                },
                            }
                        },
                        {
                            "toolUse": {
                                "toolUseId": "tu-201",
                                "name": "sentry_new_release_issues",
                                "input": {
                                    "project": service.sentry_project_id,
                                    "environment": trigger.deployment.environment.value,
                                    "release_version": trigger.deployment.release_version,
                                    "effective_since": trigger.deployment.deployed_at.isoformat(),
                                },
                            }
                        },
                        {
                            "toolUse": {
                                "toolUseId": "tu-202",
                                "name": "sentry_issue_details",
                                "input": {"issue_id": "1001"},
                            }
                        },
                    ]
                }
            },
            "stopReason": "tool_use",
        }

        prompt_fragment = build_sentry_tool_prompt_fragment(trigger.services)
        tool_uses = BedrockClient.extract_tool_uses(assistant_response)
        results, tool_messages = await execute_sentry_tool_uses(sentry_tool, tool_uses)
        conversation = [
            build_user_message(f"Check health for {service.name} in qa."),
            build_assistant_message(assistant_response),
            *tool_messages,
        ]

        assert "sentry_project_details" in prompt_fragment
        assert "project id 47" in prompt_fragment
        assert len(results) == 3
        assert results[0].tool == "sentry.project_details"
        assert results[1].tool == "sentry.new_release_issues"
        assert results[2].tool == "sentry.issue_detail"
        assert conversation[2]["content"][0]["toolResult"]["toolUseId"] == "tu-200"
        assert conversation[2]["content"][1]["toolResult"]["toolUseId"] == "tu-201"
        assert conversation[2]["content"][2]["toolResult"]["toolUseId"] == "tu-202"
