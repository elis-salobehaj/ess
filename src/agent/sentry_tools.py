"""Sentry Bedrock tool definitions and dispatch helpers.

This module is the Sentry-side tool seam between Bedrock tool-calling and the
validated REST adapter. It provides:

- Bedrock-compatible tool schemas for the release-aware Sentry issue surface.
- Pydantic validation for LLM-supplied tool inputs.
- Dispatch helpers that execute ``SentryTool`` methods and normalise results.
- Helpers to convert Bedrock ``toolUse`` blocks into ``toolResult`` messages.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from textwrap import dedent
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.llm_client import build_tool_result_message
from src.models import ServiceTarget, ToolResult
from src.tools.normalise import (
    sentry_issue_detail_to_tool_result,
    sentry_new_release_issues_to_tool_result,
    sentry_project_details_to_tool_result,
    sentry_release_details_to_tool_result,
)
from src.tools.sentry_tool import SentryResult, SentryTool


class BedrockToolUse(BaseModel):
    """Validated representation of a Bedrock ``toolUse`` block."""

    model_config = ConfigDict(extra="ignore")

    toolUseId: str = Field(min_length=1)
    name: str = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)


class _SentryToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectDetailsInput(_SentryToolInput):
    project_slug: str = Field(min_length=1)


class ReleaseDetailsInput(_SentryToolInput):
    release_version: str = Field(min_length=1)


class NewReleaseIssuesInput(_SentryToolInput):
    project: str | int
    environment: str = Field(min_length=1)
    release_version: str = Field(min_length=1)
    effective_since: datetime
    per_page: int = Field(default=20, ge=1, le=100)


class IssueDetailsInput(_SentryToolInput):
    issue_id: str = Field(min_length=1)


SentryExecutor = Callable[[SentryTool, _SentryToolInput], Awaitable[SentryResult[Any]]]
SentryNormaliser = Callable[[SentryResult[Any]], ToolResult]


@dataclass(frozen=True)
class SentryToolDefinition:
    """Definition for one Sentry Bedrock tool."""

    bedrock_name: str
    result_name: str
    description: str
    input_model: type[_SentryToolInput]
    executor: SentryExecutor
    normaliser: SentryNormaliser

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


async def _run_query_issues(
    sentry_tool: SentryTool,
    params: _SentryToolInput,
) -> SentryResult[Any]:
    assert isinstance(params, ProjectDetailsInput)
    return await sentry_tool.get_project_details(params.project_slug)


async def _run_release_details(
    sentry_tool: SentryTool,
    params: _SentryToolInput,
) -> SentryResult[Any]:
    assert isinstance(params, ReleaseDetailsInput)
    return await sentry_tool.get_release_details(params.release_version)


async def _run_new_release_issues(
    sentry_tool: SentryTool,
    params: _SentryToolInput,
) -> SentryResult[Any]:
    assert isinstance(params, NewReleaseIssuesInput)
    return await sentry_tool.query_new_release_issues(
        params.project,
        params.environment,
        params.release_version,
        params.effective_since,
        params.per_page,
    )


async def _run_issue_details(
    sentry_tool: SentryTool,
    params: _SentryToolInput,
) -> SentryResult[Any]:
    assert isinstance(params, IssueDetailsInput)
    return await sentry_tool.get_issue_details(params.issue_id)


SENTRY_TOOL_DEFINITIONS: tuple[SentryToolDefinition, ...] = (
    SentryToolDefinition(
        bedrock_name="sentry_project_details",
        result_name="project_details",
        description=(
            "Fetch Sentry project details for a service's configured project slug. "
            "Use this to validate the project mapping and capture platform/features "
            "before deeper investigation."
        ),
        input_model=ProjectDetailsInput,
        executor=_run_query_issues,
        normaliser=sentry_project_details_to_tool_result,
    ),
    SentryToolDefinition(
        bedrock_name="sentry_release_details",
        result_name="release_details",
        description=(
            "Fetch release metadata for the exact deploy release_version. Returns "
            "release creation time, newGroups, and project associations so the "
            "agent can compute the effective release start before querying issues."
        ),
        input_model=ReleaseDetailsInput,
        executor=_run_release_details,
        normaliser=sentry_release_details_to_tool_result,
    ),
    SentryToolDefinition(
        bedrock_name="sentry_new_release_issues",
        result_name="new_release_issues",
        description=(
            "List unresolved Sentry error issue groups first seen after the effective "
            "release start for a specific project, environment, and release_version. "
            "Use this after release details are known so older unresolved groups are "
            "excluded from deploy triage."
        ),
        input_model=NewReleaseIssuesInput,
        executor=_run_new_release_issues,
        normaliser=sentry_new_release_issues_to_tool_result,
    ),
    SentryToolDefinition(
        bedrock_name="sentry_issue_details",
        result_name="issue_detail",
        description=(
            "Get detailed information for a specific Sentry issue, including the "
            "latest event and stack-trace context. Use this during investigation "
            "after triage identifies a relevant issue."
        ),
        input_model=IssueDetailsInput,
        executor=_run_issue_details,
        normaliser=sentry_issue_detail_to_tool_result,
    ),
)

_TOOL_DEFINITIONS_BY_NAME = {
    definition.bedrock_name: definition for definition in SENTRY_TOOL_DEFINITIONS
}

SENTRY_TOOL_CONFIG: dict[str, Any] = {
    "tools": [definition.tool_spec() for definition in SENTRY_TOOL_DEFINITIONS]
}

_SENTRY_PROMPT_TEMPLATE = dedent(
    """
    ## Sentry Tools (REST API)

    You have access to release-aware Sentry error-tracking data through these tools:

    Triage tools:
        - sentry_project_details: validate the configured Sentry project slug and
            capture project metadata.
    - sentry_release_details: fetch release metadata for the exact deploy release_version.
        - sentry_new_release_issues: list unresolved error groups first seen after
            the effective release start.

    Investigation tools:
    - sentry_issue_details: fetch stack trace, latest event, and issue metadata.

    Datadog is the first signal. Only use Sentry after Datadog indicates an error,
    warning, or latency symptom worth investigating.
    Always use the deploy context's release_version exactly as provided.
    Use sentry_project for project-detail lookup and display, and sentry_project_id
    for org-scoped issue and event queries.
    Compute effective_since = max(deployed_at, release.dateCreated) before calling
    sentry_new_release_issues so older unresolved groups are not misclassified as
    deploy-caused.
    Do not treat a missing error.unhandled:1 slice as proof that the deploy is healthy.
    Do not take remediation actions. Observation, investigation, and reporting only.
    Summarise the tool evidence instead of repeating raw payloads verbatim.
    """
).strip()


def build_sentry_tool_prompt_fragment(services: Sequence[ServiceTarget] | None = None) -> str:
    """Build the Sentry-specific system-prompt fragment."""
    if not services:
        return _SENTRY_PROMPT_TEMPLATE

    service_lines = [
        (
            f"- Log service '{service.name}' maps to Sentry project "
            f"'{service.sentry_project}' with project id {service.sentry_project_id}."
        )
        for service in services
        if service.sentry_project
    ]
    if not service_lines:
        return _SENTRY_PROMPT_TEMPLATE

    return (
        _SENTRY_PROMPT_TEMPLATE
        + "\n\nSentry project mappings for this deployment:\n"
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


def sentry_tool_result_payload(result: ToolResult) -> dict[str, Any]:
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


async def dispatch_sentry_tool_call(
    sentry_tool: SentryTool,
    tool_name: str,
    raw_input: Mapping[str, Any],
) -> ToolResult:
    """Validate and execute a single Sentry tool call."""
    definition = _TOOL_DEFINITIONS_BY_NAME.get(tool_name)
    qualified_tool_name = f"sentry.{tool_name.removeprefix('sentry_')}"

    if definition is None:
        return _error_tool_result(
            tool_name=qualified_tool_name,
            summary=f"Unknown Sentry tool requested: {tool_name}",
            error=f"Unsupported Bedrock tool name: {tool_name}",
            raw={"input": dict(raw_input)},
        )

    try:
        validated_input = definition.input_model.model_validate(raw_input)
    except ValidationError as exc:
        return _error_tool_result(
            tool_name=f"sentry.{definition.result_name}",
            summary=f"Invalid input for {tool_name}",
            error=str(exc),
            raw={"input": dict(raw_input)},
        )

    sentry_result = await definition.executor(sentry_tool, validated_input)
    return definition.normaliser(sentry_result)


async def execute_sentry_tool_use(
    sentry_tool: SentryTool,
    tool_use: Mapping[str, Any],
) -> tuple[ToolResult, dict[str, Any]]:
    """Execute one Bedrock ``toolUse`` block and build the reply message."""
    try:
        parsed_tool_use = BedrockToolUse.model_validate(tool_use)
    except ValidationError as exc:
        raise ValueError(f"Invalid Bedrock toolUse block: {exc}") from exc

    result = await dispatch_sentry_tool_call(
        sentry_tool,
        parsed_tool_use.name,
        parsed_tool_use.input,
    )
    message = build_tool_result_message(
        parsed_tool_use.toolUseId,
        sentry_tool_result_payload(result),
        is_error=not result.success,
    )
    return result, message


async def execute_sentry_tool_uses(
    sentry_tool: SentryTool,
    tool_uses: Sequence[Mapping[str, Any]],
) -> tuple[list[ToolResult], list[dict[str, Any]]]:
    """Execute a batch of Bedrock ``toolUse`` blocks in parallel."""
    executed = await asyncio.gather(
        *(execute_sentry_tool_use(sentry_tool, tool_use) for tool_use in tool_uses)
    )
    results = [result for result, _message in executed]
    tool_result_blocks = [message["content"][0] for _result, message in executed]
    messages = []
    if tool_result_blocks:
        messages.append({"role": "user", "content": tool_result_blocks})
    return results, messages


__all__ = [
    "BedrockToolUse",
    "IssueDetailsInput",
    "NewReleaseIssuesInput",
    "ProjectDetailsInput",
    "ReleaseDetailsInput",
    "SENTRY_TOOL_CONFIG",
    "SENTRY_TOOL_DEFINITIONS",
    "build_sentry_tool_prompt_fragment",
    "dispatch_sentry_tool_call",
    "execute_sentry_tool_use",
    "execute_sentry_tool_uses",
    "sentry_tool_result_payload",
]
