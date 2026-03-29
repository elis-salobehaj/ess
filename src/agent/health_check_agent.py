"""Datadog + Sentry health-check orchestration agent.

This is the Phase 3 ESS agent loop. It keeps the shipped Datadog-first runtime
guarantees while generalising the live path into a staged Bedrock orchestrator:

- Datadog triage always runs first through the Bedrock tool layer.
- Degraded services enter a deeper investigation phase.
- Sentry-enabled degraded services gain release-aware Sentry investigation on
  the Bedrock path, with deterministic follow-up preserved as a safety rail.
- Deterministic Datadog fallback still protects the monitoring window when the
  LLM path fails or produces no tool calls.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from src.agent.datadog_tools import (
    DATADOG_TOOL_CONFIG,
    build_datadog_tool_prompt_fragment,
    execute_datadog_tool_use,
)
from src.agent.sentry_tools import (
    SENTRY_TOOL_CONFIG,
    build_sentry_tool_prompt_fragment,
    execute_sentry_tool_use,
)
from src.agent.trace import AgentTraceRecorder
from src.llm_client import BedrockClient, build_assistant_message, build_user_message
from src.models import (
    HealthCheckResult,
    HealthFinding,
    HealthSeverity,
    ServiceTarget,
    ToolResult,
)
from src.scheduler import MonitoringSession
from src.tools.normalise import (
    pup_to_tool_result,
    sentry_issue_detail_to_tool_result,
    sentry_new_release_issues_to_tool_result,
    sentry_project_details_to_tool_result,
    sentry_release_details_to_tool_result,
)
from src.tools.pup_tool import PupTool
from src.tools.sentry_tool import SentryIssue, SentryTool

logger: structlog.BoundLogger = structlog.get_logger(__name__)  # type: ignore[assignment]

_DEFAULT_MAX_ITERATIONS = 6
_DEFAULT_MAX_TOKENS_BUDGET = 50_000
_COMPACTION_THRESHOLD_RATIO = 0.8
_RECENT_MESSAGE_WINDOW = 4


@dataclass(frozen=True)
class AgentLoopOutcome:
    """Captured output from one Bedrock reasoning loop."""

    final_text: str
    executed_calls: list[tuple[dict[str, Any], ToolResult]]
    conversation: list[dict[str, Any]]


class DatadogHealthCheckAgent:
    """Run one Phase 3 health-check cycle for a monitoring session."""

    def __init__(
        self,
        bedrock_client: BedrockClient,
        pup_tool: PupTool,
        *,
        investigation_client: BedrockClient | None = None,
        sentry_tool: SentryTool | None = None,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        max_tokens_budget: int = _DEFAULT_MAX_TOKENS_BUDGET,
        trace_recorder: AgentTraceRecorder | None = None,
    ) -> None:
        self._bedrock_client = bedrock_client
        self._investigation_client = investigation_client or bedrock_client
        self._pup_tool = pup_tool
        self._sentry_tool = sentry_tool
        self._max_iterations = max_iterations
        self._max_tokens_budget = max_tokens_budget
        self._trace_recorder = trace_recorder

    async def run_health_check(self, session: MonitoringSession) -> HealthCheckResult:
        cycle_event = await self._trace(
            "cycle.started",
            session,
            attributes={
                "services": [service.name for service in session.deploy.services],
                "datadog_services": [
                    service.datadog_service_name for service in session.deploy.services
                ],
                "environment": session.deploy.deployment.environment.value,
                "regions": session.deploy.deployment.regions,
                "commit_sha": session.deploy.deployment.commit_sha,
                "release_version": session.deploy.deployment.release_version,
            },
        )

        try:
            triage_result, used_fallback = await self._run_triage_phase(
                session,
                parent_event_id=cycle_event.event_id if cycle_event else None,
            )
        except Exception as exc:
            logger.exception(
                "triage_agent_loop_failed",
                job_id=session.job_id,
                cycle=session.checks_completed + 1,
                error=str(exc),
            )
            await self._trace(
                "agent.error",
                session,
                parent_event_id=cycle_event.event_id if cycle_event else None,
                attributes={"phase": "triage", "error": str(exc)},
            )
            triage_result = await self._run_deterministic_fallback(
                session,
                reason=str(exc),
                parent_event_id=cycle_event.event_id if cycle_event else None,
            )
            used_fallback = True

        if used_fallback:
            result = await self._augment_with_sentry(
                session,
                triage_result,
                parent_event_id=cycle_event.event_id if cycle_event else None,
            )
        else:
            result = await self._run_investigation_phase(
                session,
                triage_result,
                parent_event_id=cycle_event.event_id if cycle_event else None,
            )

        await self._trace(
            "cycle.completed",
            session,
            parent_event_id=cycle_event.event_id if cycle_event else None,
            attributes={
                "overall_severity": result.overall_severity.value,
                "services_checked": result.services_checked,
                "finding_count": len(result.findings),
                "findings": [finding.model_dump(mode="json") for finding in result.findings],
            },
        )
        return result

    async def _run_triage_phase(
        self,
        session: MonitoringSession,
        *,
        parent_event_id: str | None,
    ) -> tuple[HealthCheckResult, bool]:
        outcome = await self._run_reasoning_loop(
            session,
            client=self._bedrock_client,
            system_prompt=self._build_triage_system_prompt(session),
            user_prompt=self._build_triage_user_prompt(session),
            tool_config=DATADOG_TOOL_CONFIG,
            parent_event_id=parent_event_id,
            phase="triage",
        )

        if not outcome.executed_calls:
            reason = outcome.final_text or "Bedrock returned no Datadog tool calls"
            logger.warning(
                "triage_agent_no_tool_calls",
                job_id=session.job_id,
                cycle=session.checks_completed + 1,
                reason=reason,
            )
            await self._trace(
                "fallback.triggered",
                session,
                parent_event_id=parent_event_id,
                attributes={"phase": "triage", "reason": reason},
            )
            return (
                await self._run_deterministic_fallback(
                    session,
                    reason=reason,
                    parent_event_id=parent_event_id,
                ),
                True,
            )

        return (
            self._build_result_from_executed_calls(
                session,
                outcome.executed_calls,
                final_text=outcome.final_text,
                summary_tool="agent.summary",
                fallback_reason=None,
            ),
            False,
        )

    async def _run_investigation_phase(
        self,
        session: MonitoringSession,
        triage_result: HealthCheckResult,
        *,
        parent_event_id: str | None,
    ) -> HealthCheckResult:
        services = self._services_requiring_investigation(session, triage_result)
        if not services:
            await self._trace(
                "investigation.skipped",
                session,
                parent_event_id=parent_event_id,
                attributes={"reason": "triage_healthy"},
            )
            return triage_result

        result = triage_result

        for service in services:
            service_event = await self._trace(
                "investigation.started",
                session,
                parent_event_id=parent_event_id,
                attributes={
                    "service": service.name,
                    "datadog_service": service.datadog_service_name,
                    "sentry_enabled": self._service_has_sentry_context(session, service),
                },
            )

            try:
                outcome = await self._run_service_investigation_loop(
                    session,
                    service,
                    triage_result,
                    parent_event_id=service_event.event_id if service_event else parent_event_id,
                )
            except Exception as exc:
                logger.exception(
                    "investigation_agent_loop_failed",
                    job_id=session.job_id,
                    cycle=session.checks_completed + 1,
                    service=service.name,
                    error=str(exc),
                )
                await self._trace(
                    "agent.error",
                    session,
                    parent_event_id=service_event.event_id if service_event else parent_event_id,
                    attributes={
                        "phase": "investigation",
                        "service": service.name,
                        "error": str(exc),
                    },
                )
                result = await self._merge_deterministic_sentry_follow_up(
                    session,
                    result,
                    service,
                    parent_event_id=service_event.event_id if service_event else parent_event_id,
                )
                continue

            if outcome.final_text or outcome.executed_calls:
                result = self._merge_investigation_outcome(result, service, outcome)

            if self._service_has_sentry_context(
                session, service
            ) and not self._used_sentry_tool(outcome):
                result = await self._merge_deterministic_sentry_follow_up(
                    session,
                    result,
                    service,
                    parent_event_id=service_event.event_id if service_event else parent_event_id,
                )

            await self._trace(
                "investigation.completed",
                session,
                parent_event_id=service_event.event_id if service_event else parent_event_id,
                attributes={
                    "service": service.name,
                    "tool_calls": len(outcome.executed_calls),
                    "summary_present": bool(outcome.final_text),
                },
            )

        return result

    async def _run_service_investigation_loop(
        self,
        session: MonitoringSession,
        service: ServiceTarget,
        triage_result: HealthCheckResult,
        *,
        parent_event_id: str | None,
    ) -> AgentLoopOutcome:
        has_sentry = self._service_has_sentry_context(session, service)
        tool_config = self._combined_tool_config(include_sentry=has_sentry)
        system_prompt = self._build_investigation_system_prompt(
            session,
            service,
            include_sentry=has_sentry,
        )
        user_prompt = self._build_investigation_user_prompt(session, service, triage_result)

        return await self._run_reasoning_loop(
            session,
            client=self._investigation_client,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool_config=tool_config,
            parent_event_id=parent_event_id,
            phase="investigation",
        )

    async def _run_reasoning_loop(
        self,
        session: MonitoringSession,
        *,
        client: BedrockClient,
        system_prompt: str,
        user_prompt: str,
        tool_config: dict[str, Any],
        parent_event_id: str | None,
        phase: str,
    ) -> AgentLoopOutcome:
        conversation = [build_user_message(user_prompt)]
        executed_calls: list[tuple[dict[str, Any], ToolResult]] = []
        final_text = ""

        for iteration in range(1, self._max_iterations + 1):
            request_event = await self._trace(
                "bedrock.request",
                session,
                parent_event_id=parent_event_id,
                attributes={
                    "phase": phase,
                    "iteration": iteration,
                    "model_id": self._model_id_for_trace(client),
                    "system_prompt": system_prompt,
                    "conversation": conversation,
                },
            )

            response = await client.converse(
                messages=conversation,
                system=system_prompt,
                tool_config=tool_config,
            )
            conversation.append(build_assistant_message(response))

            tool_uses = BedrockClient.extract_tool_uses(response)
            final_text = BedrockClient.extract_text(response)
            response_event = await self._trace(
                "bedrock.response",
                session,
                parent_event_id=request_event.event_id if request_event else parent_event_id,
                attributes={
                    "phase": phase,
                    "iteration": iteration,
                    "stop_reason": response.get("stopReason"),
                    "assistant_text": final_text,
                    "tool_uses": tool_uses,
                    "usage": response.get("usage", {}),
                },
            )

            if not tool_uses:
                break

            tool_results, tool_messages = await self._execute_tool_uses(
                session,
                tool_uses,
                parent_event_id=(response_event.event_id if response_event else parent_event_id),
                phase=phase,
            )
            executed_calls.extend(zip(tool_uses, tool_results, strict=False))
            conversation.extend(tool_messages)
            conversation = await self._maybe_compact_conversation(
                session,
                client=client,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                conversation=conversation,
                parent_event_id=(response_event.event_id if response_event else parent_event_id),
                phase=phase,
            )

            logger.info(
                "agent_tool_iteration",
                job_id=session.job_id,
                cycle=session.checks_completed + 1,
                phase=phase,
                iteration=iteration,
                tool_calls=len(tool_uses),
            )

        return AgentLoopOutcome(
            final_text=final_text,
            executed_calls=executed_calls,
            conversation=conversation,
        )

    async def _execute_tool_uses(
        self,
        session: MonitoringSession,
        tool_uses: list[dict[str, Any]],
        *,
        parent_event_id: str | None,
        phase: str,
    ) -> tuple[list[ToolResult], list[dict[str, Any]]]:
        executed = await asyncio.gather(
            *[
                self._execute_single_tool_use(
                    session,
                    tool_use,
                    parent_event_id=parent_event_id,
                    phase=phase,
                )
                for tool_use in tool_uses
            ]
        )
        results = [result for result, _block in executed]
        tool_result_blocks = [block for _result, block in executed]
        messages: list[dict[str, Any]] = []
        if tool_result_blocks:
            messages.append({"role": "user", "content": tool_result_blocks})
        return results, messages

    async def _execute_single_tool_use(
        self,
        session: MonitoringSession,
        tool_use: dict[str, Any],
        *,
        parent_event_id: str | None,
        phase: str,
    ) -> tuple[ToolResult, dict[str, Any]]:
        tool_name = str(tool_use.get("name", "unknown"))
        tool_input = tool_use.get("input", {})
        tool_use_event = await self._trace(
            "tool.use",
            session,
            parent_event_id=parent_event_id,
            attributes={
                "phase": phase,
                "tool_name": tool_name,
                "tool_use_id": tool_use.get("toolUseId"),
                "tool_input": tool_input,
            },
        )

        if tool_name.startswith("datadog_"):
            result, message = await execute_datadog_tool_use(self._pup_tool, tool_use)
        elif tool_name.startswith("sentry_") and self._sentry_tool is not None:
            result, message = await execute_sentry_tool_use(self._sentry_tool, tool_use)
        else:
            result = ToolResult(
                tool=tool_name.replace("_", ".", 1),
                success=False,
                data={},
                summary=f"Unsupported or unavailable tool: {tool_name}",
                error=f"No executor configured for tool {tool_name}",
                duration_ms=0,
                raw={"input": dict(tool_input)},
            )
            message = {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": tool_use.get("toolUseId", "unknown"),
                            "content": [{"text": json.dumps({"error": result.error})}],
                            "status": "error",
                        }
                    }
                ],
            }

        await self._trace(
            "tool.result",
            session,
            parent_event_id=(tool_use_event.event_id if tool_use_event else parent_event_id),
            attributes={
                "phase": phase,
                "tool": result.tool,
                "success": result.success,
                "summary": result.summary,
                "error": result.error,
                "duration_ms": result.duration_ms,
                "data": result.data,
                "raw": result.raw,
            },
        )
        return result, message["content"][0]

    async def _maybe_compact_conversation(
        self,
        session: MonitoringSession,
        *,
        client: BedrockClient,
        system_prompt: str,
        user_prompt: str,
        conversation: list[dict[str, Any]],
        parent_event_id: str | None,
        phase: str,
    ) -> list[dict[str, Any]]:
        before_tokens = self._estimate_conversation_tokens(system_prompt, conversation)
        if before_tokens <= int(self._max_tokens_budget * _COMPACTION_THRESHOLD_RATIO):
            return conversation
        if len(conversation) <= _RECENT_MESSAGE_WINDOW:
            return conversation

        older_messages = conversation[:-_RECENT_MESSAGE_WINDOW]
        recent_messages = conversation[-_RECENT_MESSAGE_WINDOW:]
        summary_text, summary_source = await self._summarise_messages(
            session,
            client=client,
            older_messages=older_messages,
            parent_event_id=parent_event_id,
            phase=phase,
        )
        compacted_prompt = build_user_message(
            user_prompt
            + "\n\nCompacted earlier evidence:\n"
            + summary_text
            + "\n\nContinue from the most recent exchange and preserve "
            + "the same observer-only rules."
        )
        compacted_conversation = [compacted_prompt, *recent_messages]
        after_tokens = self._estimate_conversation_tokens(system_prompt, compacted_conversation)

        await self._trace(
            "conversation.compacted",
            session,
            parent_event_id=parent_event_id,
            attributes={
                "phase": phase,
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
                "summary_source": summary_source,
            },
        )
        return compacted_conversation

    async def _summarise_messages(
        self,
        session: MonitoringSession,
        *,
        client: BedrockClient,
        older_messages: list[dict[str, Any]],
        parent_event_id: str | None,
        phase: str,
    ) -> tuple[str, str]:
        summary_prompt = (
            "Summarise the previous ESS health-check evidence in under 200 words. "
            "Capture degraded services, key tool evidence, and unresolved questions. "
            "Do not include chain-of-thought."
        )
        try:
            request_event = await self._trace(
                "bedrock.request",
                session,
                parent_event_id=parent_event_id,
                attributes={
                    "phase": f"{phase}_compaction",
                    "iteration": 0,
                    "model_id": self._model_id_for_trace(client),
                    "system_prompt": "Summarise prior ESS evidence for context compaction.",
                    "conversation": [*older_messages, build_user_message(summary_prompt)],
                },
            )
            response = await client.converse(
                messages=[*older_messages, build_user_message(summary_prompt)],
                system="Summarise prior ESS evidence for context compaction.",
            )
            summary_text = BedrockClient.extract_text(response).strip()
            await self._trace(
                "bedrock.response",
                session,
                parent_event_id=request_event.event_id if request_event else parent_event_id,
                attributes={
                    "phase": f"{phase}_compaction",
                    "iteration": 0,
                    "stop_reason": response.get("stopReason"),
                    "assistant_text": summary_text,
                    "tool_uses": [],
                    "usage": response.get("usage", {}),
                },
            )
            if summary_text:
                return summary_text, "bedrock"
        except Exception as exc:
            await self._trace(
                "agent.error",
                session,
                parent_event_id=parent_event_id,
                attributes={
                    "phase": f"{phase}_compaction",
                    "error": str(exc),
                },
            )

        return self._summarise_messages_locally(older_messages), "local_fallback"

    def _summarise_messages_locally(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []

        for message in messages:
            role = message.get("role")
            for block in message.get("content", []):
                if isinstance(block, dict) and "text" in block and role == "assistant":
                    text = str(block["text"]).strip().replace("\n", " ")
                    if text:
                        lines.append(text[:160])
                    continue

                tool_result = block.get("toolResult") if isinstance(block, dict) else None
                if not isinstance(tool_result, dict):
                    continue
                content = tool_result.get("content", [])
                if not isinstance(content, list) or not content:
                    continue
                payload_text = content[0].get("text") if isinstance(content[0], dict) else None
                if not isinstance(payload_text, str):
                    continue
                try:
                    payload = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue
                tool = str(payload.get("tool", "unknown"))
                summary = str(payload.get("summary") or payload.get("error") or "").strip()
                if summary:
                    lines.append(f"{tool}: {summary[:160]}")

        if not lines:
            return "No earlier evidence required compaction."
        return "Compacted earlier evidence:\n- " + "\n- ".join(lines[:8])

    async def _run_deterministic_fallback(
        self,
        session: MonitoringSession,
        *,
        reason: str,
        parent_event_id: str | None,
    ) -> HealthCheckResult:
        environment = session.deploy.deployment.environment.value
        fallback_event = await self._trace(
            "fallback.started",
            session,
            parent_event_id=parent_event_id,
            attributes={"reason": reason, "environment": environment},
        )
        per_service_results = await asyncio.gather(
            *[
                self._run_triage_for_service(service, environment)
                for service in session.deploy.services
            ]
        )

        overall_severity = HealthSeverity.HEALTHY if per_service_results else HealthSeverity.UNKNOWN
        findings: list[HealthFinding] = [
            HealthFinding(
                tool="agent.fallback",
                severity=HealthSeverity.UNKNOWN,
                summary="LLM agent loop unavailable; deterministic Datadog triage used instead.",
                details=reason,
            )
        ]
        raw_tool_outputs: dict[str, Any] = {
            "agent.fallback": {"reason": reason},
        }

        for service_name, tool_results in per_service_results:
            for index, tool_result in enumerate(tool_results, start=1):
                finding = self._tool_result_to_finding(service_name, tool_result)
                findings.append(finding)
                await self._trace(
                    "tool.result",
                    session,
                    parent_event_id=(
                        fallback_event.event_id if fallback_event else parent_event_id
                    ),
                    attributes={
                        "execution_path": "fallback",
                        "service": service_name,
                        "sequence": index,
                        "tool": tool_result.tool,
                        "success": tool_result.success,
                        "summary": tool_result.summary,
                        "error": tool_result.error,
                        "duration_ms": tool_result.duration_ms,
                        "data": tool_result.data,
                        "raw": tool_result.raw,
                    },
                )
                raw_tool_outputs[f"{service_name}:{tool_result.tool}:{index}"] = {
                    "success": tool_result.success,
                    "summary": tool_result.summary,
                    "error": tool_result.error,
                    "data": tool_result.data,
                    "raw": tool_result.raw,
                }
                overall_severity = self._max_severity(overall_severity, finding.severity)

        return HealthCheckResult(
            job_id=session.job_id,
            cycle_number=session.checks_completed + 1,
            checked_at=datetime.now(tz=UTC),
            overall_severity=overall_severity,
            findings=findings,
            services_checked=[service.name for service in session.deploy.services],
            raw_tool_outputs=raw_tool_outputs,
        )

    async def _augment_with_sentry(
        self,
        session: MonitoringSession,
        result: HealthCheckResult,
        *,
        parent_event_id: str | None,
    ) -> HealthCheckResult:
        if self._sentry_tool is None:
            await self._trace(
                "sentry.skipped",
                session,
                parent_event_id=parent_event_id,
                attributes={"reason": "tool_unconfigured"},
            )
            return result

        services = self._services_requiring_sentry(session, result)
        if not services:
            await self._trace(
                "sentry.skipped",
                session,
                parent_event_id=parent_event_id,
                attributes={"reason": "datadog_healthy_or_not_sentry_enabled"},
            )
            return result

        merged_result = result
        for service in services:
            merged_result = await self._merge_deterministic_sentry_follow_up(
                session,
                merged_result,
                service,
                parent_event_id=parent_event_id,
            )
        return merged_result

    async def _merge_deterministic_sentry_follow_up(
        self,
        session: MonitoringSession,
        result: HealthCheckResult,
        service: ServiceTarget,
        *,
        parent_event_id: str | None,
    ) -> HealthCheckResult:
        sentry_results = await self._run_sentry_investigation_for_service(
            session,
            service,
            parent_event_id=parent_event_id,
        )
        return self._merge_tool_results(
            result,
            service.name,
            sentry_results,
        )

    async def _run_sentry_investigation_for_service(
        self,
        session: MonitoringSession,
        service: ServiceTarget,
        *,
        parent_event_id: str | None,
    ) -> list[ToolResult]:
        if not self._service_has_sentry_context(session, service) or self._sentry_tool is None:
            return []

        results: list[ToolResult] = []
        project_result, project_tool_result = await self._run_sentry_call(
            session,
            parent_event_id=parent_event_id,
            tool_name="sentry_project_details",
            tool_input={"project_slug": service.sentry_project},
            invoke=lambda: self._sentry_tool.get_project_details(service.sentry_project or ""),
            normalise=sentry_project_details_to_tool_result,
        )
        results.append(project_tool_result)

        release_version = session.deploy.deployment.release_version or ""
        release_result, release_tool_result = await self._run_sentry_call(
            session,
            parent_event_id=parent_event_id,
            tool_name="sentry_release_details",
            tool_input={"release_version": release_version},
            invoke=lambda: self._sentry_tool.get_release_details(release_version),
            normalise=sentry_release_details_to_tool_result,
        )
        results.append(release_tool_result)

        if not release_result.success or release_result.data is None:
            return results

        effective_since = max(
            session.deploy.deployment.deployed_at,
            release_result.data.date_created,
        )

        issues_result, issues_tool_result = await self._run_sentry_call(
            session,
            parent_event_id=parent_event_id,
            tool_name="sentry_new_release_issues",
            tool_input={
                "project": service.sentry_project_id,
                "environment": session.deploy.deployment.environment.value,
                "release_version": release_version,
                "effective_since": effective_since.isoformat(),
                "per_page": 20,
            },
            invoke=lambda: self._sentry_tool.query_new_release_issues(
                service.sentry_project_id or 0,
                session.deploy.deployment.environment.value,
                release_version,
                effective_since,
                20,
            ),
            normalise=sentry_new_release_issues_to_tool_result,
        )
        results.append(issues_tool_result)

        if not issues_result.success or not issues_result.data:
            return results

        for issue in self._top_issue_candidates(issues_result.data)[:3]:
            _detail_result, detail_tool_result = await self._run_sentry_call(
                session,
                parent_event_id=parent_event_id,
                tool_name="sentry_issue_details",
                tool_input={"issue_id": issue.id},
                invoke=lambda issue_id=issue.id: self._sentry_tool.get_issue_details(issue_id),
                normalise=sentry_issue_detail_to_tool_result,
            )
            results.append(detail_tool_result)

        return results

    async def _run_sentry_call(
        self,
        session: MonitoringSession,
        *,
        parent_event_id: str | None,
        tool_name: str,
        tool_input: dict[str, Any],
        invoke: Any,
        normalise: Any,
    ) -> tuple[Any, ToolResult]:
        tool_use_event = await self._trace(
            "tool.use",
            session,
            parent_event_id=parent_event_id,
            attributes={
                "execution_path": "sentry_follow_up",
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
        )
        raw_result = await invoke()
        tool_result = normalise(raw_result)
        await self._trace(
            "tool.result",
            session,
            parent_event_id=(tool_use_event.event_id if tool_use_event else parent_event_id),
            attributes={
                "execution_path": "sentry_follow_up",
                "tool": tool_result.tool,
                "success": tool_result.success,
                "summary": tool_result.summary,
                "error": tool_result.error,
                "duration_ms": tool_result.duration_ms,
                "data": tool_result.data,
                "raw": tool_result.raw,
            },
        )
        return raw_result, tool_result

    def _build_triage_system_prompt(self, session: MonitoringSession) -> str:
        base_prompt = (
            "You are ESS, a post-deploy monitoring agent. "
            "This is the triage phase. Start with Datadog tools across every service. "
            "Use Datadog evidence to determine whether any service is degraded right now. "
            "If a service looks degraded, leave that for the investigation phase instead of "
            "guessing at a root cause. Never take remediation actions. "
            "Observation and reporting only. "
            "After you finish, respond with a concise final assessment whose first line is "
            "exactly 'Severity: HEALTHY', 'Severity: WARNING', 'Severity: CRITICAL', or "
            "'Severity: UNKNOWN'."
        )
        return base_prompt + "\n\n" + build_datadog_tool_prompt_fragment(session.deploy.services)

    def _build_triage_user_prompt(self, session: MonitoringSession) -> str:
        deploy = session.deploy.deployment
        service_lines = [
            (
                f"- log_service={service.name}, datadog_service={service.datadog_service_name}, "
                f"infrastructure={service.infrastructure.value}"
            )
            for service in session.deploy.services
        ]
        previous_summary = ""
        if session.results:
            last_result = session.results[-1]
            previous_summary = (
                f"\nPrevious cycle severity: {last_result.overall_severity.value}. "
                f"Previous findings: {len(last_result.findings)}."
            )

        return (
            "Run triage cycle "
            f"{session.checks_completed + 1} for deployment {session.job_id}.\n"
            f"Environment: {deploy.environment.value}\n"
            f"Regions: {', '.join(deploy.regions) if deploy.regions else 'none'}\n"
            f"Commit: {deploy.commit_sha}\n"
            f"Release: {deploy.release_version or 'none'}\n"
            f"Deployed at: {deploy.deployed_at.isoformat()}\n"
            f"Services:\n" + "\n".join(service_lines) + previous_summary + "\n"
            "Use Datadog tools to determine whether the deployment is healthy right now."
        )

    def _build_investigation_system_prompt(
        self,
        session: MonitoringSession,
        service: ServiceTarget,
        *,
        include_sentry: bool,
    ) -> str:
        base_prompt = (
            "You are ESS, a post-deploy monitoring agent continuing the investigation phase for a "
            "single degraded service. Use the already-observed Datadog degradation as the trigger "
            "for deeper evidence collection. Prefer investigation-time Datadog tools such as "
            "incidents, "
            "infrastructure health, and APM operations. "
            "If the service is Sentry-enabled, use release-aware Sentry tools for the exact deploy "
            "release. "
            "Never take remediation actions. Observation and reporting only. "
            "After you finish, respond with a concise final assessment whose first line is "
            "exactly 'Severity: HEALTHY', 'Severity: WARNING', 'Severity: CRITICAL', or "
            "'Severity: UNKNOWN'."
        )
        fragments = [base_prompt, build_datadog_tool_prompt_fragment([service])]
        if include_sentry:
            fragments.append(build_sentry_tool_prompt_fragment([service]))
        return "\n\n".join(fragments)

    def _build_investigation_user_prompt(
        self,
        session: MonitoringSession,
        service: ServiceTarget,
        triage_result: HealthCheckResult,
    ) -> str:
        deploy = session.deploy.deployment
        service_findings = [
            finding.summary
            for finding in triage_result.findings
            if finding.summary.startswith(f"{service.name}: ")
        ]
        prior_cycle_summary = ""
        if session.results:
            last_result = session.results[-1]
            prior_cycle_summary = (
                f"\nPrevious cycle severity: {last_result.overall_severity.value}. "
                f"Previous findings: {len(last_result.findings)}."
            )

        sentry_context = "Sentry disabled for this service."
        if self._service_has_sentry_context(session, service):
            sentry_context = (
                f"Sentry project: {service.sentry_project} "
                f"(project id {service.sentry_project_id}), release={deploy.release_version}."
            )

        findings_text = "\n".join(f"- {summary}" for summary in service_findings) or "- none"
        return (
            f"Investigate degraded service {service.name} for deployment {session.job_id}.\n"
            f"Environment: {deploy.environment.value}\n"
            f"Datadog service: {service.datadog_service_name}\n"
            f"Infrastructure: {service.infrastructure.value}\n"
            f"Commit: {deploy.commit_sha}\n"
            f"Release: {deploy.release_version or 'none'}\n"
            f"Deployed at: {deploy.deployed_at.isoformat()}\n"
            f"{sentry_context}{prior_cycle_summary}\n"
            "Triage findings for this service:\n"
            f"{findings_text}\n"
            "Collect deeper evidence, correlate it to the deploy timing, and finish "
            "with a concise severity line and investigation summary."
        )

    def _build_result_from_executed_calls(
        self,
        session: MonitoringSession,
        executed_calls: list[tuple[dict[str, Any], ToolResult]],
        *,
        final_text: str,
        summary_tool: str,
        fallback_reason: str | None,
    ) -> HealthCheckResult:
        datadog_name_to_service = {
            service.datadog_service_name: service.name for service in session.deploy.services
        }
        findings: list[HealthFinding] = []
        raw_tool_outputs: dict[str, Any] = {}
        overall_severity = HealthSeverity.UNKNOWN if not executed_calls else HealthSeverity.HEALTHY

        if final_text:
            summary_severity = self._severity_from_agent_text(final_text)
            findings.append(
                HealthFinding(
                    tool=summary_tool,
                    severity=summary_severity or HealthSeverity.UNKNOWN,
                    summary=final_text.splitlines()[0][:300],
                    details=final_text,
                )
            )
            raw_tool_outputs[summary_tool] = {"text": final_text}
            if summary_severity is not None:
                overall_severity = self._max_severity(overall_severity, summary_severity)

        if fallback_reason:
            raw_tool_outputs["agent.fallback"] = {"reason": fallback_reason}

        for index, (tool_use, tool_result) in enumerate(executed_calls, start=1):
            raw_input = tool_use.get("input", {})
            datadog_service_name = raw_input.get("service")
            service_name = datadog_name_to_service.get(datadog_service_name, datadog_service_name)
            display_name = service_name or "global"
            finding = self._tool_result_to_finding(display_name, tool_result)
            findings.append(finding)
            raw_tool_outputs[f"{display_name}:{tool_result.tool}:{index}"] = {
                "tool_use": tool_use,
                "success": tool_result.success,
                "summary": tool_result.summary,
                "error": tool_result.error,
                "data": tool_result.data,
                "raw": tool_result.raw,
            }
            overall_severity = self._max_severity(overall_severity, finding.severity)

        return HealthCheckResult(
            job_id=session.job_id,
            cycle_number=session.checks_completed + 1,
            checked_at=datetime.now(tz=UTC),
            overall_severity=overall_severity,
            findings=findings,
            services_checked=[service.name for service in session.deploy.services],
            raw_tool_outputs=raw_tool_outputs,
        )

    def _merge_investigation_outcome(
        self,
        base_result: HealthCheckResult,
        service: ServiceTarget,
        outcome: AgentLoopOutcome,
    ) -> HealthCheckResult:
        merged = base_result
        if outcome.final_text:
            summary_severity = self._severity_from_agent_text(outcome.final_text)
            merged = self._append_finding(
                merged,
                HealthFinding(
                    tool="agent.investigation_summary",
                    severity=summary_severity or HealthSeverity.UNKNOWN,
                    summary=f"{service.name}: {outcome.final_text.splitlines()[0][:300]}",
                    details=outcome.final_text,
                ),
                key_prefix=f"{service.name}:agent.investigation_summary",
                raw_payload={"text": outcome.final_text},
                elevate_unknown=False,
            )

        tool_results = [tool_result for _tool_use, tool_result in outcome.executed_calls]
        return self._merge_tool_results(merged, service.name, tool_results)

    def _merge_tool_results(
        self,
        base_result: HealthCheckResult,
        service_name: str,
        tool_results: list[ToolResult],
    ) -> HealthCheckResult:
        merged = base_result
        for tool_result in tool_results:
            finding = self._tool_result_to_finding(service_name, tool_result)
            merged = self._append_finding(
                merged,
                finding,
                key_prefix=f"{service_name}:{tool_result.tool}",
                raw_payload={
                    "success": tool_result.success,
                    "summary": tool_result.summary,
                    "error": tool_result.error,
                    "data": tool_result.data,
                    "raw": tool_result.raw,
                },
                elevate_unknown=False,
            )
        return merged

    def _append_finding(
        self,
        base_result: HealthCheckResult,
        finding: HealthFinding,
        *,
        key_prefix: str,
        raw_payload: dict[str, Any],
        elevate_unknown: bool,
    ) -> HealthCheckResult:
        findings = list(base_result.findings)
        findings.append(finding)
        raw_tool_outputs = dict(base_result.raw_tool_outputs)
        next_index = len(raw_tool_outputs) + 1
        raw_tool_outputs[f"{key_prefix}:{next_index}"] = raw_payload
        overall_severity = base_result.overall_severity
        if finding.severity in (
            HealthSeverity.WARNING,
            HealthSeverity.CRITICAL,
        ) or (elevate_unknown and finding.severity == HealthSeverity.UNKNOWN):
            overall_severity = self._max_severity(overall_severity, finding.severity)

        return HealthCheckResult(
            job_id=base_result.job_id,
            cycle_number=base_result.cycle_number,
            checked_at=base_result.checked_at,
            overall_severity=overall_severity,
            findings=findings,
            services_checked=base_result.services_checked,
            raw_tool_outputs=raw_tool_outputs,
        )

    def _services_requiring_investigation(
        self,
        session: MonitoringSession,
        result: HealthCheckResult,
    ) -> list[ServiceTarget]:
        service_lookup = {service.name: service for service in session.deploy.services}
        degraded_names: list[str] = []

        for service in session.deploy.services:
            if any(
                finding.summary.startswith(f"{service.name}: ")
                and finding.severity in (HealthSeverity.WARNING, HealthSeverity.CRITICAL)
                for finding in result.findings
            ):
                degraded_names.append(service.name)

        if not degraded_names and result.overall_severity in (
            HealthSeverity.WARNING,
            HealthSeverity.CRITICAL,
        ):
            degraded_names = [service.name for service in session.deploy.services]

        seen: set[str] = set()
        ordered_names = [name for name in degraded_names if not (name in seen or seen.add(name))]
        return [service_lookup[name] for name in ordered_names]

    def _services_requiring_sentry(
        self,
        session: MonitoringSession,
        result: HealthCheckResult,
    ) -> list[ServiceTarget]:
        return [
            service
            for service in self._services_requiring_investigation(session, result)
            if self._service_has_sentry_context(session, service)
        ]

    def _service_has_sentry_context(
        self,
        session: MonitoringSession,
        service: ServiceTarget,
    ) -> bool:
        return (
            self._sentry_tool is not None
            and service.sentry_project is not None
            and service.sentry_project_id is not None
            and session.deploy.deployment.release_version is not None
        )

    def _used_sentry_tool(self, outcome: AgentLoopOutcome) -> bool:
        return any(
            tool_result.tool.startswith("sentry.")
            for _tool_use, tool_result in outcome.executed_calls
        )

    @staticmethod
    def _combined_tool_config(*, include_sentry: bool) -> dict[str, Any]:
        tools = list(DATADOG_TOOL_CONFIG["tools"])
        if include_sentry:
            tools.extend(SENTRY_TOOL_CONFIG["tools"])
        return {"tools": tools}

    @staticmethod
    def _top_issue_candidates(issues: list[SentryIssue]) -> list[SentryIssue]:
        return sorted(
            issues,
            key=lambda issue: (
                issue.count,
                issue.first_seen or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )

    async def _trace(
        self,
        event_type: str,
        session: MonitoringSession,
        *,
        parent_event_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        if self._trace_recorder is None:
            return None
        return await self._trace_recorder.emit(
            event_type,
            trace_id=session.job_id,
            cycle_number=session.checks_completed + 1,
            parent_event_id=parent_event_id,
            attributes=attributes,
        )

    def _model_id_for_trace(self, client: BedrockClient) -> str:
        model_id = getattr(client, "model_id", None)
        if isinstance(model_id, str) and model_id:
            return model_id

        private_model_id = getattr(client, "_model_id", None)
        if isinstance(private_model_id, str) and private_model_id:
            return private_model_id

        return "unknown"

    async def _run_triage_for_service(
        self,
        service: ServiceTarget,
        environment: str,
    ) -> tuple[str, list[ToolResult]]:
        monitor_result, log_result, apm_result = await asyncio.gather(
            self._pup_tool.get_monitor_status(service.datadog_service_name, environment),
            self._pup_tool.search_error_logs(service.datadog_service_name),
            self._pup_tool.get_apm_stats(service.datadog_service_name, environment),
        )
        return service.name, [
            pup_to_tool_result(monitor_result, "monitor_status"),
            pup_to_tool_result(log_result, "error_logs"),
            pup_to_tool_result(apm_result, "apm_stats"),
        ]

    @staticmethod
    def _tool_result_to_finding(service_name: str, tool_result: ToolResult) -> HealthFinding:
        severity = DatadogHealthCheckAgent._severity_from_tool_result(tool_result)
        return HealthFinding(
            tool=tool_result.tool,
            severity=severity,
            summary=f"{service_name}: {tool_result.summary}",
            details=tool_result.error,
        )

    @staticmethod
    def _severity_from_agent_text(text: str) -> HealthSeverity | None:
        upper = text.upper()
        for severity in (
            HealthSeverity.CRITICAL,
            HealthSeverity.WARNING,
            HealthSeverity.HEALTHY,
            HealthSeverity.UNKNOWN,
        ):
            if f"SEVERITY: {severity.value}" in upper:
                return severity
        return None

    @staticmethod
    def _severity_from_tool_result(tool_result: ToolResult) -> HealthSeverity:
        if not tool_result.success:
            return HealthSeverity.UNKNOWN

        payload = json.dumps(tool_result.data).lower()

        if tool_result.tool == "datadog.monitor_status":
            if any(token in payload for token in ("alert", "critical")):
                return HealthSeverity.CRITICAL
            if any(token in payload for token in ("warn", "warning", "no data")):
                return HealthSeverity.WARNING
            return HealthSeverity.HEALTHY

        if tool_result.tool in {
            "datadog.error_logs",
            "datadog.incidents",
            "datadog.apm_operations",
        }:
            return (
                HealthSeverity.WARNING
                if DatadogHealthCheckAgent._estimate_collection_size(tool_result.data) > 0
                else HealthSeverity.HEALTHY
            )

        if tool_result.tool == "sentry.new_release_issues":
            return (
                HealthSeverity.WARNING
                if DatadogHealthCheckAgent._estimate_collection_size(tool_result.data) > 0
                else HealthSeverity.HEALTHY
            )

        if tool_result.tool == "sentry.issue_detail":
            return HealthSeverity.WARNING

        if tool_result.tool == "datadog.infrastructure_health":
            if any(token in payload for token in ("critical", "unhealthy", "down")):
                return HealthSeverity.CRITICAL
            if any(token in payload for token in ("warn", "warning", "degraded")):
                return HealthSeverity.WARNING

        return HealthSeverity.HEALTHY

    @staticmethod
    def _estimate_collection_size(payload: dict[str, Any]) -> int:
        for key in ("items", "logs", "data", "results", "entries", "operations"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                for nested in value.values():
                    if isinstance(nested, list):
                        return len(nested)
        return 0

    def _estimate_conversation_tokens(
        self,
        system_prompt: str,
        conversation: list[dict[str, Any]],
    ) -> int:
        payload = json.dumps(
            {
                "system": system_prompt,
                "conversation": conversation,
            },
            default=str,
        )
        return max(1, len(payload) // 4)

    @staticmethod
    def _max_severity(left: HealthSeverity, right: HealthSeverity) -> HealthSeverity:
        order = {
            HealthSeverity.HEALTHY: 0,
            HealthSeverity.WARNING: 1,
            HealthSeverity.CRITICAL: 2,
            HealthSeverity.UNKNOWN: 3,
        }
        return left if order[left] >= order[right] else right


HealthCheckAgent = DatadogHealthCheckAgent
