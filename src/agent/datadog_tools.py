"""Datadog Bedrock tool definitions and dispatch helpers.

This module is the D3 seam between Bedrock tool-calling and the Datadog
Pup adapter. It provides:

- Bedrock-compatible tool schemas for the Datadog tool surface.
- Pydantic validation for LLM-supplied tool inputs.
- Dispatch helpers that execute PupTool methods and normalise the results.
- Helpers to convert Bedrock ``toolUse`` blocks into ``toolResult`` messages.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from textwrap import dedent
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.llm_client import build_tool_result_message
from src.models import Environment, ServiceTarget, ToolResult
from src.tools.normalise import pup_to_tool_result
from src.tools.pup_tool import PupResult, PupTool


class BedrockToolUse(BaseModel):
    """Validated representation of a Bedrock ``toolUse`` block."""

    model_config = ConfigDict(extra="forbid")

    toolUseId: str = Field(min_length=1)
    name: str = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)


class _DatadogToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MonitorStatusInput(_DatadogToolInput):
    service: str = Field(min_length=1)
    environment: Environment


class ErrorLogsInput(_DatadogToolInput):
    service: str = Field(min_length=1)
    minutes_back: int = Field(default=10, ge=1, le=120)


class APMStatsInput(_DatadogToolInput):
    service: str = Field(min_length=1)
    environment: Environment


class IncidentsInput(_DatadogToolInput):
    pass


class InfrastructureHealthInput(_DatadogToolInput):
    service: str = Field(min_length=1)


class APMOperationsInput(_DatadogToolInput):
    service: str = Field(min_length=1)
    environment: Environment


DatadogExecutor = Callable[[PupTool, _DatadogToolInput], Awaitable[PupResult]]


@dataclass(frozen=True)
class DatadogToolDefinition:
    """Definition for one Datadog Bedrock tool."""

    bedrock_name: str
    result_name: str
    description: str
    input_model: type[_DatadogToolInput]
    executor: DatadogExecutor

    def tool_spec(self) -> dict[str, Any]:
        schema = _inline_local_refs(self.input_model.model_json_schema())
        schema.pop("title", None)
        return {
            "toolSpec": {
                "name": self.bedrock_name,
                "description": self.description,
                "inputSchema": {"json": schema},
            }
        }


def _inline_local_refs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.pop("$defs", {})

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                definition_name = ref.removeprefix("#/$defs/")
                if definition_name in defs:
                    return _walk(defs[definition_name])
            return {key: _walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    return _walk(schema)


async def _run_monitor_status(pup_tool: PupTool, params: _DatadogToolInput) -> PupResult:
    assert isinstance(params, MonitorStatusInput)
    return await pup_tool.get_monitor_status(params.service, params.environment.value)


async def _run_error_logs(pup_tool: PupTool, params: _DatadogToolInput) -> PupResult:
    assert isinstance(params, ErrorLogsInput)
    return await pup_tool.search_error_logs(params.service, minutes=params.minutes_back)


async def _run_apm_stats(pup_tool: PupTool, params: _DatadogToolInput) -> PupResult:
    assert isinstance(params, APMStatsInput)
    return await pup_tool.get_apm_stats(params.service, params.environment.value)


async def _run_incidents(pup_tool: PupTool, params: _DatadogToolInput) -> PupResult:
    assert isinstance(params, IncidentsInput)
    return await pup_tool.get_recent_incidents()


async def _run_infrastructure_health(
    pup_tool: PupTool, params: _DatadogToolInput
) -> PupResult:
    assert isinstance(params, InfrastructureHealthInput)
    return await pup_tool.get_infrastructure_health(params.service)


async def _run_apm_operations(pup_tool: PupTool, params: _DatadogToolInput) -> PupResult:
    assert isinstance(params, APMOperationsInput)
    return await pup_tool.get_apm_operations(params.service, params.environment.value)


DATADOG_TOOL_DEFINITIONS: tuple[DatadogToolDefinition, ...] = (
    DatadogToolDefinition(
        bedrock_name="datadog_monitor_status",
        result_name="monitor_status",
        description=(
            "Check Datadog monitor status for a service. Returns monitors tagged "
            "with the Datadog service name and environment, including their "
            "current state."
        ),
        input_model=MonitorStatusInput,
        executor=_run_monitor_status,
    ),
    DatadogToolDefinition(
        bedrock_name="datadog_error_logs",
        result_name="error_logs",
        description=(
            "Search Datadog logs for recent error-level entries for a service. "
            "Use this during triage to detect new post-deploy errors."
        ),
        input_model=ErrorLogsInput,
        executor=_run_error_logs,
    ),
    DatadogToolDefinition(
        bedrock_name="datadog_apm_stats",
        result_name="apm_stats",
        description=(
            "Get Datadog APM performance statistics for a service, including "
            "latency, error rate, and throughput."
        ),
        input_model=APMStatsInput,
        executor=_run_apm_stats,
    ),
    DatadogToolDefinition(
        bedrock_name="datadog_incidents",
        result_name="incidents",
        description=(
            "List active Datadog incidents to see whether an existing incident "
            "already explains the post-deploy issue."
        ),
        input_model=IncidentsInput,
        executor=_run_incidents,
    ),
    DatadogToolDefinition(
        bedrock_name="datadog_infrastructure_health",
        result_name="infrastructure_health",
        description=(
            "Check Datadog host health for hosts running a service. Use this "
            "during investigation to rule out CPU, memory, or disk pressure."
        ),
        input_model=InfrastructureHealthInput,
        executor=_run_infrastructure_health,
    ),
    DatadogToolDefinition(
        bedrock_name="datadog_apm_operations",
        result_name="apm_operations",
        description=(
            "Get per-operation Datadog APM breakdown for a service to identify "
            "slow endpoints or high-error routes."
        ),
        input_model=APMOperationsInput,
        executor=_run_apm_operations,
    ),
)

_TOOL_DEFINITIONS_BY_NAME = {
    definition.bedrock_name: definition for definition in DATADOG_TOOL_DEFINITIONS
}

DATADOG_TOOL_CONFIG: dict[str, Any] = {
    "tools": [definition.tool_spec() for definition in DATADOG_TOOL_DEFINITIONS]
}

_DATADOG_PROMPT_TEMPLATE = dedent(
    """
    ## Datadog Tools (via Pup CLI)

    You have access to Datadog observability data through these tools:

    Triage tools:
    - datadog_monitor_status: check whether service-tagged monitors are alerting.
    - datadog_error_logs: search recent Datadog error logs for the service.
    - datadog_apm_stats: inspect latency, error rate, and throughput.

    Investigation tools:
    - datadog_incidents: check whether Datadog already has an open incident.
    - datadog_infrastructure_health: rule out host-level CPU, memory, or disk issues.
    - datadog_apm_operations: identify the slow or erroring endpoint.

    Always use the deploy context's datadog_service_name value for Datadog tool calls.
    Do not take remediation actions. Observation, investigation, and reporting only.
    Summarise the tool evidence instead of repeating raw payloads verbatim.
    """
).strip()


def build_datadog_tool_prompt_fragment(services: Sequence[ServiceTarget] | None = None) -> str:
    """Build the Datadog-specific system-prompt fragment.

    When service mappings are provided, append an explicit list of log-service
    to Datadog-service mappings so the model does not use the wrong name.
    """
    if not services:
        return _DATADOG_PROMPT_TEMPLATE

    service_lines = [
        f"- Log service '{service.name}' maps to Datadog service '{service.datadog_service_name}'."
        for service in services
    ]
    return (
        _DATADOG_PROMPT_TEMPLATE
        + "\n\nDatadog service mappings for this deployment:\n"
        + "\n".join(service_lines)
    )


def _error_tool_result(
    tool_name: str,
    summary: str,
    error: str,
    raw: Mapping[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        tool=tool_name,
        success=False,
        data={},
        summary=summary,
        error=error,
        duration_ms=0,
        raw=dict(raw or {}),
    )


def datadog_tool_result_payload(result: ToolResult) -> dict[str, Any]:
    """Convert a normalised ``ToolResult`` into Bedrock toolResult content."""
    payload: dict[str, Any] = {
        "tool": result.tool,
        "success": result.success,
        "summary": result.summary,
        "data": result.data,
        "duration_ms": result.duration_ms,
    }
    if result.error is not None:
        payload["error"] = result.error
    if result.raw:
        payload["raw"] = result.raw
    return payload


async def dispatch_datadog_tool_call(
    pup_tool: PupTool,
    tool_name: str,
    raw_input: Mapping[str, Any],
) -> ToolResult:
    """Validate and execute a single Datadog tool call."""
    definition = _TOOL_DEFINITIONS_BY_NAME.get(tool_name)
    qualified_tool_name = f"datadog.{tool_name.removeprefix('datadog_')}"

    if definition is None:
        return _error_tool_result(
            tool_name=qualified_tool_name,
            summary=f"Unknown Datadog tool requested: {tool_name}",
            error=f"Unsupported Bedrock tool name: {tool_name}",
            raw={"input": dict(raw_input)},
        )

    try:
        validated_input = definition.input_model.model_validate(raw_input)
    except ValidationError as exc:
        return _error_tool_result(
            tool_name=f"datadog.{definition.result_name}",
            summary=f"Invalid input for {tool_name}",
            error=str(exc),
            raw={"input": dict(raw_input)},
        )

    pup_result = await definition.executor(pup_tool, validated_input)
    return pup_to_tool_result(pup_result, definition.result_name)


async def execute_datadog_tool_use(
    pup_tool: PupTool,
    tool_use: Mapping[str, Any],
) -> tuple[ToolResult, dict[str, Any]]:
    """Execute one Bedrock ``toolUse`` block and build the reply message."""
    try:
        parsed_tool_use = BedrockToolUse.model_validate(tool_use)
    except ValidationError as exc:
        raise ValueError(f"Invalid Bedrock toolUse block: {exc}") from exc

    result = await dispatch_datadog_tool_call(
        pup_tool,
        parsed_tool_use.name,
        parsed_tool_use.input,
    )
    message = build_tool_result_message(
        parsed_tool_use.toolUseId,
        datadog_tool_result_payload(result),
        is_error=not result.success,
    )
    return result, message


async def execute_datadog_tool_uses(
    pup_tool: PupTool,
    tool_uses: Sequence[Mapping[str, Any]],
) -> tuple[list[ToolResult], list[dict[str, Any]]]:
    """Execute a batch of Bedrock ``toolUse`` blocks in parallel."""
    executed = await asyncio.gather(
        *(execute_datadog_tool_use(pup_tool, tool_use) for tool_use in tool_uses)
    )
    results = [result for result, _message in executed]
    messages = [message for _result, message in executed]
    return results, messages


__all__ = [
    "BedrockToolUse",
    "DATADOG_TOOL_CONFIG",
    "DATADOG_TOOL_DEFINITIONS",
    "APMOperationsInput",
    "APMStatsInput",
    "ErrorLogsInput",
    "InfrastructureHealthInput",
    "IncidentsInput",
    "MonitorStatusInput",
    "build_datadog_tool_prompt_fragment",
    "datadog_tool_result_payload",
    "dispatch_datadog_tool_call",
    "execute_datadog_tool_use",
    "execute_datadog_tool_uses",
]
