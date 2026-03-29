"""Datadog-first health-check agent.

This is the first real agent loop wired into ESS. It uses Bedrock tool-calling
with the Datadog D3 tool layer when available, falls back to deterministic Pup
triage when the LLM path fails, and runs release-aware Sentry follow-up only
after Datadog indicates a deploy symptom worth investigating.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import structlog

from src.agent.datadog_tools import (
    DATADOG_TOOL_CONFIG,
    build_datadog_tool_prompt_fragment,
    execute_datadog_tool_uses,
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


class DatadogHealthCheckAgent:
    """Run one Datadog-only health-check cycle for a monitoring session."""

    def __init__(
        self,
        bedrock_client: BedrockClient,
        pup_tool: PupTool,
        *,
        sentry_tool: SentryTool | None = None,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        trace_recorder: AgentTraceRecorder | None = None,
    ) -> None:
        self._bedrock_client = bedrock_client
        self._pup_tool = pup_tool
        self._sentry_tool = sentry_tool
        self._max_iterations = max_iterations
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
            result = await self._run_agent_loop(
                session,
                parent_event_id=cycle_event.event_id if cycle_event else None,
            )
        except Exception as exc:
            logger.exception(
                "datadog_agent_loop_failed",
                job_id=session.job_id,
                cycle=session.checks_completed + 1,
                error=str(exc),
            )
            await self._trace(
                "agent.error",
                session,
                parent_event_id=cycle_event.event_id if cycle_event else None,
                attributes={"error": str(exc)},
            )
            result = await self._run_deterministic_fallback(
                session,
                reason=str(exc),
                parent_event_id=cycle_event.event_id if cycle_event else None,
            )

        result = await self._augment_with_sentry(
            session,
            result,
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

    async def _run_agent_loop(
        self,
        session: MonitoringSession,
        *,
        parent_event_id: str | None,
    ) -> HealthCheckResult:
        system_prompt = self._build_system_prompt(session)
        user_prompt = self._build_user_prompt(session)
        conversation = [build_user_message(user_prompt)]
        executed_calls: list[tuple[dict[str, Any], ToolResult]] = []
        final_text = ""

        for iteration in range(1, self._max_iterations + 1):
            request_event = await self._trace(
                "bedrock.request",
                session,
                parent_event_id=parent_event_id,
                attributes={
                    "iteration": iteration,
                    "model_id": self._model_id_for_trace(),
                    "system_prompt": system_prompt,
                    "conversation": conversation,
                },
            )
            response = await self._bedrock_client.converse(
                messages=conversation,
                system=system_prompt,
                tool_config=DATADOG_TOOL_CONFIG,
            )
            conversation.append(build_assistant_message(response))

            tool_uses = BedrockClient.extract_tool_uses(response)
            final_text = BedrockClient.extract_text(response)
            response_event = await self._trace(
                "bedrock.response",
                session,
                parent_event_id=request_event.event_id if request_event else parent_event_id,
                attributes={
                    "iteration": iteration,
                    "stop_reason": response.get("stopReason"),
                    "assistant_text": final_text,
                    "tool_uses": tool_uses,
                    "usage": response.get("usage", {}),
                },
            )
            if tool_uses:
                tool_results, tool_messages = await execute_datadog_tool_uses(
                    self._pup_tool,
                    tool_uses,
                )
                conversation.extend(tool_messages)
                executed_calls.extend(zip(tool_uses, tool_results, strict=False))
                for tool_use, tool_result in zip(tool_uses, tool_results, strict=False):
                    tool_use_event = await self._trace(
                        "tool.use",
                        session,
                        parent_event_id=(
                            response_event.event_id if response_event else parent_event_id
                        ),
                        attributes={
                            "iteration": iteration,
                            "tool_name": tool_use.get("name"),
                            "tool_use_id": tool_use.get("toolUseId"),
                            "tool_input": tool_use.get("input", {}),
                        },
                    )
                    await self._trace(
                        "tool.result",
                        session,
                        parent_event_id=(
                            tool_use_event.event_id
                            if tool_use_event
                            else response_event.event_id
                            if response_event
                            else parent_event_id
                        ),
                        attributes={
                            "iteration": iteration,
                            "tool": tool_result.tool,
                            "success": tool_result.success,
                            "summary": tool_result.summary,
                            "error": tool_result.error,
                            "duration_ms": tool_result.duration_ms,
                            "data": tool_result.data,
                            "raw": tool_result.raw,
                        },
                    )
                logger.info(
                    "datadog_agent_tool_iteration",
                    job_id=session.job_id,
                    cycle=session.checks_completed + 1,
                    iteration=iteration,
                    tool_calls=len(tool_uses),
                )
                continue

            break

        if not executed_calls:
            reason = final_text or "Bedrock returned no Datadog tool calls"
            logger.warning(
                "datadog_agent_no_tool_calls",
                job_id=session.job_id,
                cycle=session.checks_completed + 1,
                reason=reason,
            )
            await self._trace(
                "fallback.triggered",
                session,
                parent_event_id=parent_event_id,
                attributes={"reason": reason},
            )
            return await self._run_deterministic_fallback(
                session,
                reason=reason,
                parent_event_id=parent_event_id,
            )

        return self._build_result_from_executed_calls(
            session,
            executed_calls,
            final_text=final_text,
            fallback_reason=None,
        )

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

        findings = list(result.findings)
        raw_tool_outputs = dict(result.raw_tool_outputs)
        overall_severity = result.overall_severity
        output_index = len(raw_tool_outputs)

        for service in services:
            sentry_results = await self._run_sentry_investigation_for_service(
                session,
                service,
                parent_event_id=parent_event_id,
            )
            for tool_result in sentry_results:
                output_index += 1
                finding = self._tool_result_to_finding(service.name, tool_result)
                findings.append(finding)
                raw_tool_outputs[f"{service.name}:{tool_result.tool}:{output_index}"] = {
                    "success": tool_result.success,
                    "summary": tool_result.summary,
                    "error": tool_result.error,
                    "data": tool_result.data,
                    "raw": tool_result.raw,
                }
                if finding.severity in (HealthSeverity.WARNING, HealthSeverity.CRITICAL):
                    overall_severity = self._max_severity(overall_severity, finding.severity)

        return HealthCheckResult(
            job_id=result.job_id,
            cycle_number=result.cycle_number,
            checked_at=result.checked_at,
            overall_severity=overall_severity,
            findings=findings,
            services_checked=result.services_checked,
            raw_tool_outputs=raw_tool_outputs,
        )

    async def _run_sentry_investigation_for_service(
        self,
        session: MonitoringSession,
        service: ServiceTarget,
        *,
        parent_event_id: str | None,
    ) -> list[ToolResult]:
        if (
            self._sentry_tool is None
            or service.sentry_project is None
            or service.sentry_project_id is None
            or session.deploy.deployment.release_version is None
        ):
            return []

        results: list[ToolResult] = []
        project_result, project_tool_result = await self._run_sentry_call(
            session,
            parent_event_id=parent_event_id,
            tool_name="sentry_project_details",
            tool_input={"project_slug": service.sentry_project},
            invoke=lambda: self._sentry_tool.get_project_details(service.sentry_project),
            normalise=sentry_project_details_to_tool_result,
        )
        results.append(project_tool_result)

        release_version = session.deploy.deployment.release_version
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
                service.sentry_project_id,
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

    def _services_requiring_sentry(
        self,
        session: MonitoringSession,
        result: HealthCheckResult,
    ) -> list[ServiceTarget]:
        sentry_enabled = {
            service.name: service
            for service in session.deploy.services
            if service.sentry_project and service.sentry_project_id is not None
        }
        if not sentry_enabled:
            return []

        degraded_service_names = [
            service.name
            for service in session.deploy.services
            if service.name in sentry_enabled
            and any(
                finding.tool.startswith("datadog.")
                and finding.severity in (HealthSeverity.WARNING, HealthSeverity.CRITICAL)
                and finding.summary.startswith(f"{service.name}: ")
                for finding in result.findings
            )
        ]

        if not degraded_service_names and result.overall_severity in (
            HealthSeverity.WARNING,
            HealthSeverity.CRITICAL,
        ):
            degraded_service_names = list(sentry_enabled.keys())

        return [sentry_enabled[name] for name in degraded_service_names]

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

    def _model_id_for_trace(self) -> str:
        model_id = getattr(self._bedrock_client, "model_id", None)
        if isinstance(model_id, str) and model_id:
            return model_id

        private_model_id = getattr(self._bedrock_client, "_model_id", None)
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

    def _build_system_prompt(self, session: MonitoringSession) -> str:
        base_prompt = (
            "You are ESS, a post-deploy monitoring agent. "
            "You must assess deployment health using only Datadog tools currently available. "
            "Always use Datadog tools before concluding. Start with triage tools for each service. "
            "If triage reveals anomalies, investigate deeper with incidents, "
            "infrastructure health, "
            "or APM operations. Never take remediation actions. Observation and reporting only. "
            "After you finish, respond with a concise final assessment whose first line is "
            "exactly 'Severity: HEALTHY', 'Severity: WARNING', 'Severity: CRITICAL', or "
            "'Severity: UNKNOWN'."
        )
        return base_prompt + "\n\n" + build_datadog_tool_prompt_fragment(session.deploy.services)

    def _build_user_prompt(self, session: MonitoringSession) -> str:
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
            "Run health-check cycle "
            f"{session.checks_completed + 1} for deployment {session.job_id}.\n"
            f"Environment: {deploy.environment.value}\n"
            f"Regions: {', '.join(deploy.regions) if deploy.regions else 'none'}\n"
            f"Commit: {deploy.commit_sha}\n"
            f"Release: {deploy.release_version or 'none'}\n"
            f"Deployed at: {deploy.deployed_at.isoformat()}\n"
            f"Services:\n" + "\n".join(service_lines) + previous_summary + "\n"
            "Use Datadog tools to determine whether the deployment is healthy right now."
        )

    def _build_result_from_executed_calls(
        self,
        session: MonitoringSession,
        executed_calls: list[tuple[dict[str, Any], ToolResult]],
        *,
        final_text: str,
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
                    tool="agent.summary",
                    severity=summary_severity or HealthSeverity.UNKNOWN,
                    summary=final_text.splitlines()[0][:300],
                    details=final_text,
                )
            )
            raw_tool_outputs["agent.summary"] = {"text": final_text}
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

        if tool_result.tool in {"datadog.error_logs", "datadog.incidents"}:
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
        for key in ("items", "logs", "data", "results", "entries"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                for nested in value.values():
                    if isinstance(nested, list):
                        return len(nested)
        return 0

    @staticmethod
    def _max_severity(left: HealthSeverity, right: HealthSeverity) -> HealthSeverity:
        order = {
            HealthSeverity.HEALTHY: 0,
            HealthSeverity.WARNING: 1,
            HealthSeverity.CRITICAL: 2,
            HealthSeverity.UNKNOWN: 3,
        }
        return left if order[left] >= order[right] else right
