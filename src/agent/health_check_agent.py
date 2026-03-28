"""Datadog-backed health-check agent.

This is the first real agent loop wired into ESS. It uses Bedrock tool-calling
with the Datadog D3 tool layer when available, and falls back to deterministic
Pup triage so monitoring still produces a usable result if the LLM path fails.
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
from src.llm_client import BedrockClient, build_assistant_message, build_user_message
from src.models import (
    HealthCheckResult,
    HealthFinding,
    HealthSeverity,
    ServiceTarget,
    ToolResult,
)
from src.scheduler import MonitoringSession
from src.tools.normalise import pup_to_tool_result
from src.tools.pup_tool import PupTool

logger: structlog.BoundLogger = structlog.get_logger(__name__)  # type: ignore[assignment]

_DEFAULT_MAX_ITERATIONS = 6


class DatadogHealthCheckAgent:
    """Run one Datadog-only health-check cycle for a monitoring session."""

    def __init__(
        self,
        bedrock_client: BedrockClient,
        pup_tool: PupTool,
        *,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    ) -> None:
        self._bedrock_client = bedrock_client
        self._pup_tool = pup_tool
        self._max_iterations = max_iterations

    async def run_health_check(self, session: MonitoringSession) -> HealthCheckResult:
        try:
            return await self._run_agent_loop(session)
        except Exception as exc:
            logger.exception(
                "datadog_agent_loop_failed",
                job_id=session.job_id,
                cycle=session.checks_completed + 1,
                error=str(exc),
            )
            return await self._run_deterministic_fallback(session, reason=str(exc))

    async def _run_agent_loop(self, session: MonitoringSession) -> HealthCheckResult:
        system_prompt = self._build_system_prompt(session)
        conversation = [build_user_message(self._build_user_prompt(session))]
        executed_calls: list[tuple[dict[str, Any], ToolResult]] = []
        final_text = ""

        for iteration in range(1, self._max_iterations + 1):
            response = await self._bedrock_client.converse(
                messages=conversation,
                system=system_prompt,
                tool_config=DATADOG_TOOL_CONFIG,
            )
            conversation.append(build_assistant_message(response))

            tool_uses = BedrockClient.extract_tool_uses(response)
            if tool_uses:
                tool_results, tool_messages = await execute_datadog_tool_uses(
                    self._pup_tool,
                    tool_uses,
                )
                conversation.extend(tool_messages)
                executed_calls.extend(zip(tool_uses, tool_results, strict=False))
                logger.info(
                    "datadog_agent_tool_iteration",
                    job_id=session.job_id,
                    cycle=session.checks_completed + 1,
                    iteration=iteration,
                    tool_calls=len(tool_uses),
                )
                continue

            final_text = BedrockClient.extract_text(response)
            break

        if not executed_calls:
            reason = final_text or "Bedrock returned no Datadog tool calls"
            logger.warning(
                "datadog_agent_no_tool_calls",
                job_id=session.job_id,
                cycle=session.checks_completed + 1,
                reason=reason,
            )
            return await self._run_deterministic_fallback(session, reason=reason)

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
    ) -> HealthCheckResult:
        environment = session.deploy.deployment.environment.value
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
