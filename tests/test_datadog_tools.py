"""Tests for Datadog Bedrock tool definitions and dispatch helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent.datadog_tools import (
    DATADOG_TOOL_CONFIG,
    build_datadog_tool_prompt_fragment,
    datadog_tool_result_payload,
    dispatch_datadog_tool_call,
    execute_datadog_tool_use,
    execute_datadog_tool_uses,
)
from src.llm_client import BedrockClient, build_assistant_message, build_user_message
from src.models import DeployTrigger, ServiceTarget
from src.tools.pup_tool import PupResult

_EXAMPLE_TRIGGER_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "examples"
    / "triggers"
    / "pason-well-service-qa-e2e.json"
)


def _ok_pup_result(data: dict | list | None = None) -> PupResult:
    return PupResult(
        command="pup test command",
        exit_code=0,
        data=data if data is not None else {"metadata": {"description": "tool ok"}},
        stderr="",
        duration_ms=25,
    )


def _mock_pup_tool() -> SimpleNamespace:
    return SimpleNamespace(
        get_monitor_status=AsyncMock(return_value=_ok_pup_result({"monitors": []})),
        search_error_logs=AsyncMock(return_value=_ok_pup_result({"logs": []})),
        get_apm_stats=AsyncMock(return_value=_ok_pup_result({"stats": []})),
        get_recent_incidents=AsyncMock(return_value=_ok_pup_result([{"id": "INC-1"}])),
        get_infrastructure_health=AsyncMock(return_value=_ok_pup_result({"hosts": []})),
        get_apm_operations=AsyncMock(return_value=_ok_pup_result({"operations": []})),
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


class TestDatadogToolConfig:
    def test_exports_bedrock_tool_config_with_expected_names(self) -> None:
        tool_specs = DATADOG_TOOL_CONFIG["tools"]
        tool_names = {tool["toolSpec"]["name"] for tool in tool_specs}

        assert tool_names == {
            "datadog_monitor_status",
            "datadog_error_logs",
            "datadog_apm_stats",
            "datadog_incidents",
            "datadog_infrastructure_health",
            "datadog_apm_operations",
        }

    def test_error_logs_schema_uses_default_minutes_back(self) -> None:
        error_logs_spec = next(
            tool["toolSpec"]
            for tool in DATADOG_TOOL_CONFIG["tools"]
            if tool["toolSpec"]["name"] == "datadog_error_logs"
        )
        schema = error_logs_spec["inputSchema"]["json"]

        assert schema["required"] == ["service"]
        assert schema["properties"]["minutes_back"]["default"] == 10

    def test_monitor_status_schema_exposes_supported_environments(self) -> None:
        monitor_spec = next(
            tool["toolSpec"]
            for tool in DATADOG_TOOL_CONFIG["tools"]
            if tool["toolSpec"]["name"] == "datadog_monitor_status"
        )
        environment_schema = monitor_spec["inputSchema"]["json"]["properties"]["environment"]

        assert environment_schema["enum"] == [
            "production",
            "staging",
            "development",
            "qa",
        ]

    def test_tool_schemas_are_self_contained_for_bedrock(self) -> None:
        assert _contains_schema_ref(DATADOG_TOOL_CONFIG) is False


class TestBuildDatadogToolPromptFragment:
    def test_appends_service_name_mapping(self) -> None:
        fragment = build_datadog_tool_prompt_fragment(
            [
                ServiceTarget(
                    name="hub-ca-auth",
                    datadog_service_name="example-auth-service",
                )
            ]
        )

        assert "datadog_monitor_status" in fragment
        assert "Observation, investigation, and reporting only." in fragment
        assert "hub-ca-auth" in fragment
        assert "example-auth-service" in fragment

    def test_uses_real_example_trigger_service_mapping(self) -> None:
        trigger = _load_example_trigger()

        fragment = build_datadog_tool_prompt_fragment(trigger.services)

        assert "pason-well-service" in fragment
        assert "Datadog service mappings for this deployment" in fragment


class TestDispatchDatadogToolCall:
    @pytest.mark.asyncio
    async def test_monitor_status_dispatches_and_normalises_output(self) -> None:
        pup_tool = _mock_pup_tool()

        result = await dispatch_datadog_tool_call(
            pup_tool,
            "datadog_monitor_status",
            {"service": "example-auth-service", "environment": "production"},
        )

        pup_tool.get_monitor_status.assert_awaited_once_with(
            "example-auth-service",
            "production",
        )
        assert result.success is True
        assert result.tool == "datadog.monitor_status"
        assert result.data == {"monitors": []}

    @pytest.mark.asyncio
    async def test_error_logs_uses_default_minutes_back(self) -> None:
        pup_tool = _mock_pup_tool()

        result = await dispatch_datadog_tool_call(
            pup_tool,
            "datadog_error_logs",
            {"service": "example-auth-service"},
        )

        pup_tool.search_error_logs.assert_awaited_once_with(
            "example-auth-service",
            minutes=10,
        )
        assert result.tool == "datadog.error_logs"

    @pytest.mark.asyncio
    async def test_invalid_environment_returns_error_without_calling_tool(self) -> None:
        pup_tool = _mock_pup_tool()

        result = await dispatch_datadog_tool_call(
            pup_tool,
            "datadog_apm_stats",
            {"service": "example-auth-service", "environment": "sandbox"},
        )

        pup_tool.get_apm_stats.assert_not_awaited()
        assert result.success is False
        assert result.tool == "datadog.apm_stats"
        assert "environment" in (result.error or "")

    @pytest.mark.asyncio
    async def test_unknown_tool_name_returns_error_result(self) -> None:
        pup_tool = _mock_pup_tool()

        result = await dispatch_datadog_tool_call(
            pup_tool,
            "datadog_not_real",
            {"service": "example-auth-service"},
        )

        assert result.success is False
        assert result.tool == "datadog.not_real"
        assert "Unknown Datadog tool requested" in result.summary


class TestToolResultPayload:
    def test_preserves_error_details_when_present(self) -> None:
        payload = datadog_tool_result_payload(
            result=SimpleNamespace(
                tool="datadog.error_logs",
                success=False,
                summary="search failed",
                data={},
                duration_ms=0,
                error="timeout",
                raw={"input": {"service": "svc"}},
            )
        )

        assert payload["error"] == "timeout"
        assert payload["raw"] == {"input": {"service": "svc"}}


class TestExecuteDatadogToolUse:
    @pytest.mark.asyncio
    async def test_builds_tool_result_message(self) -> None:
        pup_tool = _mock_pup_tool()

        result, message = await execute_datadog_tool_use(
            pup_tool,
            {
                "toolUseId": "tu-001",
                "name": "datadog_monitor_status",
                "input": {"service": "example-auth-service", "environment": "production"},
            },
        )

        tool_result = message["content"][0]["toolResult"]
        payload = json.loads(tool_result["content"][0]["text"])

        assert result.tool == "datadog.monitor_status"
        assert tool_result["toolUseId"] == "tu-001"
        assert "status" not in tool_result
        assert payload["tool"] == "datadog.monitor_status"
        assert payload["success"] is True

    @pytest.mark.asyncio
    async def test_invalid_tool_input_marks_message_as_error(self) -> None:
        pup_tool = _mock_pup_tool()

        result, message = await execute_datadog_tool_use(
            pup_tool,
            {
                "toolUseId": "tu-002",
                "name": "datadog_apm_stats",
                "input": {"service": "example-auth-service", "environment": "invalid"},
            },
        )

        assert result.success is False
        assert message["content"][0]["toolResult"]["status"] == "error"


class TestExecuteDatadogToolUses:
    @pytest.mark.asyncio
    async def test_mock_llm_tool_use_round_trip(self) -> None:
        pup_tool = _mock_pup_tool()
        assistant_response = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tu-100",
                                "name": "datadog_monitor_status",
                                "input": {
                                    "service": "example-auth-service",
                                    "environment": "production",
                                },
                            }
                        },
                        {
                            "toolUse": {
                                "toolUseId": "tu-101",
                                "name": "datadog_error_logs",
                                "input": {"service": "example-auth-service", "minutes_back": 15},
                            }
                        },
                    ]
                }
            },
            "stopReason": "tool_use",
        }

        tool_uses = BedrockClient.extract_tool_uses(assistant_response)
        results, tool_messages = await execute_datadog_tool_uses(pup_tool, tool_uses)
        conversation = [
            build_user_message("check the deployment"),
            build_assistant_message(assistant_response),
            *tool_messages,
        ]

        assert len(results) == 2
        assert results[0].tool == "datadog.monitor_status"
        assert results[1].tool == "datadog.error_logs"
        assert conversation[1]["role"] == "assistant"
        assert conversation[2]["content"][0]["toolResult"]["toolUseId"] == "tu-100"
        assert conversation[3]["content"][0]["toolResult"]["toolUseId"] == "tu-101"
        pup_tool.search_error_logs.assert_awaited_once_with(
            "example-auth-service",
            minutes=15,
        )

    @pytest.mark.asyncio
    async def test_mock_llm_round_trip_with_real_trigger_example(self) -> None:
        trigger = _load_example_trigger()
        pup_tool = _mock_pup_tool()
        service = trigger.services[0]
        environment = trigger.deployment.environment.value

        assistant_response = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tu-200",
                                "name": "datadog_monitor_status",
                                "input": {
                                    "service": service.datadog_service_name,
                                    "environment": environment,
                                },
                            }
                        },
                        {
                            "toolUse": {
                                "toolUseId": "tu-201",
                                "name": "datadog_apm_stats",
                                "input": {
                                    "service": service.datadog_service_name,
                                    "environment": environment,
                                },
                            }
                        },
                    ]
                }
            },
            "stopReason": "tool_use",
        }

        prompt_fragment = build_datadog_tool_prompt_fragment(trigger.services)
        tool_uses = BedrockClient.extract_tool_uses(assistant_response)
        results, tool_messages = await execute_datadog_tool_uses(pup_tool, tool_uses)
        conversation = [
            build_user_message(f"Check health for {service.name} in {environment}."),
            build_assistant_message(assistant_response),
            *tool_messages,
        ]

        assert "pason-well-service" in prompt_fragment
        assert len(results) == 2
        assert results[0].tool == "datadog.monitor_status"
        assert results[1].tool == "datadog.apm_stats"
        assert conversation[2]["content"][0]["toolResult"]["toolUseId"] == "tu-200"
        assert conversation[3]["content"][0]["toolResult"]["toolUseId"] == "tu-201"
        pup_tool.get_monitor_status.assert_awaited_once_with(
            service.datadog_service_name,
            environment,
        )
        pup_tool.get_apm_stats.assert_awaited_once_with(
            service.datadog_service_name,
            environment,
        )
