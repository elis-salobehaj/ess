"""MS Teams notification policy, rich card building, and delivery.

Phase 4 extends the shipped Phase 1.5 path with richer Adaptive Cards,
investigation follow-up posts, and bounded webhook retries while keeping the
same incoming-webhook transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote, urlparse

import aiohttp
import structlog
from pydantic import BaseModel, ConfigDict, Field

from src.models import HealthCheckResult, HealthSeverity
from src.scheduler import MonitoringSession

if TYPE_CHECKING:
    from src.config import ESSConfig

logger: structlog.BoundLogger = structlog.get_logger(__name__)  # type: ignore[assignment]

_TEAMS_RESPONSE_PREVIEW_LIMIT = 500
_MAX_CARD_ACTIONS = 3


class TeamsDeliveryMode(StrEnum):
    ALL = "all"
    REAL_WORLD = "real-world"


class NotificationKind(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"
    SUMMARY = "summary"
    INVESTIGATION = "investigation"


class NotificationLink(BaseModel):
    """A clickable link rendered into the Adaptive Card actions area."""

    model_config = ConfigDict(extra="forbid")

    title: str
    url: str


class NotificationDecision(BaseModel):
    """A policy decision to notify Teams."""

    model_config = ConfigDict(extra="forbid")

    kind: NotificationKind
    headline: str
    summary: str
    overall_severity: HealthSeverity
    reason: str
    cycle_number: int | None = None
    target_services: list[str] = Field(default_factory=list)
    finding_summaries: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    timeline_entries: list[str] = Field(default_factory=list)
    investigation_summary: str | None = None
    related_notification_key: str | None = None
    links: list[NotificationLink] = Field(default_factory=list)


class TeamsAttachment(BaseModel):
    """Top-level Teams attachment envelope."""

    contentType: Literal["application/vnd.microsoft.card.adaptive"]
    content: dict[str, Any]


class TeamsMessage(BaseModel):
    """Validated Teams incoming webhook payload."""

    type: Literal["message"] = "message"
    attachments: list[TeamsAttachment]


class TeamsDeliveryResult(BaseModel):
    """Outcome of a Teams webhook delivery attempt."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    status_code: int | None = None
    response_text: str = ""
    error: str | None = None
    attempts: int = 1


class TeamsPublisher:
    """Bounded async Teams webhook publisher."""

    def __init__(
        self,
        *,
        timeout_seconds: int = 10,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        transport=None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._transport = transport
        self._sleep = sleep

    async def post_card(
        self,
        webhook_url: str,
        card: dict[str, Any],
    ) -> TeamsDeliveryResult:
        payload = TeamsMessage(
            attachments=[
                TeamsAttachment(
                    contentType="application/vnd.microsoft.card.adaptive",
                    content=card,
                )
            ]
        ).model_dump(exclude_none=True)

        attempt = 0
        while True:
            attempt += 1
            status_code: int | None = None
            response_preview = ""
            error: str | None = None
            retryable = False

            try:
                status_code, response_text = await self._post_payload(webhook_url, payload)
                response_preview = response_text[:_TEAMS_RESPONSE_PREVIEW_LIMIT]
                if 200 <= status_code < 300:
                    logger.info(
                        "teams_delivery_ok",
                        status_code=status_code,
                        attempts=attempt,
                    )
                    return TeamsDeliveryResult(
                        ok=True,
                        status_code=status_code,
                        response_text=response_preview,
                        attempts=attempt,
                    )

                error = response_preview or f"Teams webhook returned HTTP {status_code}"
                retryable = self._is_retryable_status(status_code)
            except Exception as exc:
                error = str(exc)
                retryable = True
                logger.warning(
                    "teams_delivery_exception",
                    error=error,
                    attempt=attempt,
                    timeout_seconds=self._timeout_seconds,
                )

            if retryable and attempt <= self._retry_attempts:
                delay = self._retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "teams_delivery_retrying",
                    attempt=attempt,
                    retry_delay_seconds=delay,
                    status_code=status_code,
                    error=error,
                )
                await self._sleep(delay)
                continue

            logger.warning(
                "teams_delivery_failed",
                status_code=status_code,
                response_text=response_preview,
                error=error,
                attempts=attempt,
            )
            return TeamsDeliveryResult(
                ok=False,
                status_code=status_code,
                response_text=response_preview,
                error=error,
                attempts=attempt,
            )

    async def _post_payload(
        self,
        webhook_url: str,
        payload: dict[str, Any],
    ) -> tuple[int, str]:
        if self._transport is not None:
            return await self._transport(webhook_url, payload, self._timeout_seconds)

        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(
                webhook_url,
                json=payload,
            ) as response,
        ):
            return response.status, await response.text()

    @staticmethod
    def _is_retryable_status(status_code: int | None) -> bool:
        if status_code is None:
            return True
        if status_code in {408, 425, 429}:
            return True
        return status_code >= 500


def resolve_webhook_url(
    session: MonitoringSession,
    default_webhook_url: str | None,
) -> tuple[str | None, str]:
    payload_url = session.deploy.monitoring.teams_webhook_url
    if payload_url:
        return payload_url, "trigger_payload"
    if default_webhook_url:
        return default_webhook_url, "default_config"
    return None, "missing"


def resolve_teams_delivery_mode(
    config: ESSConfig,
    session: MonitoringSession,
) -> TeamsDeliveryMode:
    raw_mode = str(session.deploy.extra_context.get("teams_mode") or config.teams_delivery_mode)
    normalised = raw_mode.strip().lower().replace("_", "-")
    if normalised == TeamsDeliveryMode.ALL:
        return TeamsDeliveryMode.ALL
    return TeamsDeliveryMode.REAL_WORLD


def supports_thread_replies(
    _config: ESSConfig,
    _session: MonitoringSession,
) -> bool:
    return False


def evaluate_cycle_notification(
    session: MonitoringSession,
    result: HealthCheckResult,
) -> tuple[NotificationDecision | None, str]:
    if result.overall_severity == HealthSeverity.CRITICAL:
        return (
            NotificationDecision(
                kind=NotificationKind.CRITICAL,
                headline="ESS detected a critical deploy issue",
                summary=_build_cycle_summary(session, result),
                overall_severity=result.overall_severity,
                reason="critical_immediate",
                cycle_number=result.cycle_number,
                target_services=result.services_checked,
                finding_summaries=_finding_summaries(result),
                recommendations=_recommendations_from_result(result),
            ),
            "critical_immediate",
        )

    if result.overall_severity == HealthSeverity.WARNING:
        streak = _warning_streak(session.results)
        if streak == 2:
            return (
                NotificationDecision(
                    kind=NotificationKind.WARNING,
                    headline="ESS observed repeated deploy warnings",
                    summary=_build_cycle_summary(session, result),
                    overall_severity=result.overall_severity,
                    reason="warning_repeated",
                    cycle_number=result.cycle_number,
                    target_services=result.services_checked,
                    finding_summaries=_finding_summaries(result),
                    recommendations=_recommendations_from_result(result),
                ),
                "warning_repeated",
            )
        if streak < 2:
            return None, "warning_threshold_not_reached"
        return None, "warning_already_notified_for_streak"

    return None, "no_notification_required"


def build_investigation_notification(
    session: MonitoringSession,
    result: HealthCheckResult,
    alert_decision: NotificationDecision,
) -> NotificationDecision | None:
    investigation_findings = [
        finding for finding in result.findings if finding.tool == "agent.investigation_summary"
    ]
    if not investigation_findings:
        return None

    target_services = _unique_strings(
        _service_name_from_summary(finding.summary) for finding in investigation_findings
    )
    if not target_services:
        target_services = list(result.services_checked)

    investigation_summary = "\n\n".join(
        finding.details or finding.summary for finding in investigation_findings
    ).strip()

    return NotificationDecision(
        kind=NotificationKind.INVESTIGATION,
        headline="ESS investigation follow-up",
        summary=_build_investigation_summary(session, result, target_services),
        overall_severity=result.overall_severity,
        reason="investigation_follow_up",
        cycle_number=result.cycle_number,
        target_services=target_services,
        finding_summaries=_finding_summaries_for_services(result, target_services),
        recommendations=_recommendations_from_result(result),
        investigation_summary=investigation_summary,
        related_notification_key=(
            f"{session.job_id}:{alert_decision.kind.value}:{alert_decision.cycle_number}"
        ),
    )


def build_summary_notification(session: MonitoringSession) -> NotificationDecision:
    overall = _aggregate_severity(session.results)
    latest_findings = _finding_summaries(session.results[-1]) if session.results else []
    return NotificationDecision(
        kind=NotificationKind.SUMMARY,
        headline="ESS monitoring window complete",
        summary=_build_window_summary(session, overall),
        overall_severity=overall,
        reason="monitoring_window_complete",
        cycle_number=session.checks_completed or None,
        target_services=[service.name for service in session.deploy.services],
        finding_summaries=latest_findings,
        recommendations=_recommendations_from_session(session),
        timeline_entries=_build_timeline_entries(session),
    )


def build_completion_warning_notification(
    session: MonitoringSession,
) -> NotificationDecision | None:
    if not session.results:
        return None

    overall = _aggregate_severity(session.results)
    if overall != HealthSeverity.WARNING:
        return None

    latest_warning = next(
        (
            result
            for result in reversed(session.results)
            if result.overall_severity == HealthSeverity.WARNING
        ),
        None,
    )
    if latest_warning is None:
        return None

    degraded_cycles = sum(
        1 for result in session.results if result.overall_severity == HealthSeverity.WARNING
    )
    return NotificationDecision(
        kind=NotificationKind.WARNING,
        headline="ESS observed repeated deploy warnings",
        summary=(
            f"Monitoring completed with non-critical deploy warnings for "
            f"{_display_services(latest_warning.services_checked)} in "
            f"{session.deploy.deployment.environment.value} after {_release_reference(session)}. "
            f"Warning cycles: {degraded_cycles}/{session.checks_completed}."
        ),
        overall_severity=HealthSeverity.WARNING,
        reason="warning_deferred_until_completion",
        cycle_number=session.checks_completed or latest_warning.cycle_number,
        target_services=latest_warning.services_checked,
        finding_summaries=_finding_summaries(latest_warning),
        recommendations=_recommendations_from_session(session),
        timeline_entries=_build_timeline_entries(session),
    )


def build_teams_card(
    config: ESSConfig,
    session: MonitoringSession,
    decision: NotificationDecision,
) -> dict[str, Any]:
    facts = _build_card_facts(session, decision)
    context_label = _notification_context_label(config, session)

    body: list[dict[str, Any]] = [
        *([
            {
                "type": "TextBlock",
                "text": context_label,
                "isSubtle": True,
                "spacing": "None",
                "wrap": True,
            }
        ] if context_label else []),
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": decision.headline,
            "color": _teams_color(decision.overall_severity),
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": decision.summary,
            "wrap": True,
        },
        *([
            {
                "type": "FactSet",
                "facts": facts,
            }
        ] if facts else []),
    ]

    if decision.related_notification_key:
        body.append(
            {
                "type": "TextBlock",
                "text": f"Related alert: {decision.related_notification_key}",
                "isSubtle": True,
                "wrap": True,
                "spacing": "Small",
            }
        )

    if decision.investigation_summary:
        body.append(
            {
                "type": "TextBlock",
                "text": "Investigation\n" + _trim_multiline_text(decision.investigation_summary, 5),
                "wrap": True,
            }
        )

    if decision.finding_summaries:
        body.append(
            {
                "type": "TextBlock",
                "text": "Signals\n- " + "\n- ".join(decision.finding_summaries[:3]),
                "wrap": True,
            }
        )

    if decision.timeline_entries:
        body.append(
            {
                "type": "TextBlock",
                "text": "Recent cycles\n- " + "\n- ".join(decision.timeline_entries[:3]),
                "wrap": True,
            }
        )

    if decision.recommendations:
        body.append(
            {
                "type": "TextBlock",
                "text": "Next\n- " + "\n- ".join(decision.recommendations[:2]),
                "wrap": True,
            }
        )

    actions = [
        {
            "type": "Action.OpenUrl",
            "title": link.title,
            "url": link.url,
        }
        for link in _build_notification_links(config, session, decision)
    ]

    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.2",
        "fallbackText": f"{decision.headline}: {decision.summary}",
        "msteams": {"width": "Full"},
        "body": body,
    }
    if actions:
        card["actions"] = actions[:_MAX_CARD_ACTIONS]
    return card


def _build_cycle_summary(session: MonitoringSession, result: HealthCheckResult) -> str:
    release_ref = _release_reference(session)
    services = _display_services(result.services_checked)
    findings = len(result.findings)
    if result.overall_severity == HealthSeverity.CRITICAL:
        return (
            f"Critical signal for {services} in {session.deploy.deployment.environment.value} "
            f"after {release_ref}. {findings} findings require immediate review."
        )
    return (
        f"Second warning cycle for {services} in {session.deploy.deployment.environment.value} "
        f"after {release_ref}. {findings} findings need review."
    )


def _build_investigation_summary(
    session: MonitoringSession,
    result: HealthCheckResult,
    target_services: list[str],
) -> str:
    services = _display_services(target_services or result.services_checked)
    return (
        f"Deeper investigation linked the alert to {services} after "
        f"{_release_reference(session)}."
    )


def _build_window_summary(
    session: MonitoringSession,
    overall: HealthSeverity,
) -> str:
    degraded_cycles = sum(
        1 for result in session.results if result.overall_severity != HealthSeverity.HEALTHY
    )
    services = _display_services(service.name for service in session.deploy.services)
    return (
        f"Window complete for {services} in {session.deploy.deployment.environment.value}. "
        f"Verdict: {overall.value}. Degraded cycles: {degraded_cycles}/{session.checks_completed}."
    )


def _finding_summaries(result: HealthCheckResult) -> list[str]:
    return [finding.summary for finding in result.findings[:5]]


def _finding_summaries_for_services(
    result: HealthCheckResult,
    service_names: list[str],
) -> list[str]:
    if not service_names:
        return _finding_summaries(result)

    summaries: list[str] = []
    for finding in result.findings:
        service_name = _service_name_from_summary(finding.summary)
        if service_name not in service_names:
            continue
        summaries.append(finding.summary)
        if len(summaries) == 5:
            break
    return summaries


def _warning_streak(results: list[HealthCheckResult]) -> int:
    streak = 0
    for result in reversed(results):
        if result.overall_severity == HealthSeverity.WARNING:
            streak += 1
            continue
        break
    return streak


def _aggregate_severity(results: list[HealthCheckResult]) -> HealthSeverity:
    order = {
        HealthSeverity.HEALTHY: 0,
        HealthSeverity.WARNING: 1,
        HealthSeverity.CRITICAL: 2,
        HealthSeverity.UNKNOWN: 3,
    }
    overall = HealthSeverity.UNKNOWN if not results else HealthSeverity.HEALTHY
    for result in results:
        if order[result.overall_severity] > order[overall]:
            overall = result.overall_severity
    return overall


def _build_timeline_entries(session: MonitoringSession) -> list[str]:
    return [
        (
            f"Cycle {result.cycle_number}: {result.overall_severity.value} "
            f"with {len(result.findings)} findings"
        )
        for result in session.results[-6:]
    ]


def _recommendations_from_session(session: MonitoringSession) -> list[str]:
    overall = _aggregate_severity(session.results)
    if overall == HealthSeverity.HEALTHY:
        return ["No action required. Monitoring completed without actionable deploy issues."]

    recommendations: list[str] = []
    if any(
        finding.tool.startswith("sentry.")
        for result in session.results
        for finding in result.findings
    ):
        recommendations.append(
            "Review the release-aware Sentry evidence for the affected services."
        )
    recommendations.append(
        "Use the cycle timeline to focus on the first degraded check in the window."
    )
    if overall == HealthSeverity.CRITICAL:
        recommendations.append("Escalate to the owning team immediately.")
    else:
        recommendations.append("Continue monitoring while the owning team investigates.")
    return _unique_strings(recommendations)


def _recommendations_from_result(result: HealthCheckResult) -> list[str]:
    recommendations: list[str] = []
    if any(finding.tool.startswith("sentry.") for finding in result.findings):
        recommendations.append("Investigate the new release issues in Sentry.")
    if any(
        finding.tool in {
            "datadog.apm_operations",
            "datadog.error_logs",
            "datadog.monitor_status",
        }
        for finding in result.findings
    ):
        recommendations.append(
            "Review Datadog service, logs, and APM views for the affected routes."
        )
    if result.overall_severity == HealthSeverity.CRITICAL:
        recommendations.append("Escalate to the owning team immediately.")
    elif result.overall_severity == HealthSeverity.WARNING:
        recommendations.append(
            "Continue monitoring the deploy window while the owning team investigates."
        )
    else:
        recommendations.append("No action required beyond normal monitoring.")
    return _unique_strings(recommendations)


def _decision_services(session: MonitoringSession, decision: NotificationDecision) -> list[str]:
    if decision.target_services:
        return decision.target_services
    return [service.name for service in session.deploy.services]


def _build_notification_links(
    config: ESSConfig,
    session: MonitoringSession,
    decision: NotificationDecision,
) -> list[NotificationLink]:
    target_services = set(_decision_services(session, decision))
    multi_service = len(target_services) > 1
    links = list(decision.links)
    sentry_base = _sentry_ui_base(config)

    for service in session.deploy.services:
        if service.name not in target_services:
            continue

        links.append(
            NotificationLink(
                title=(f"Datadog: {service.name}" if multi_service else "Datadog"),
                url=_datadog_service_url(
                    config.dd_site,
                    service.datadog_service_name,
                    session.deploy.deployment.environment.value,
                ),
            )
        )

        if service.sentry_project:
            links.append(
                NotificationLink(
                    title=(f"Sentry: {service.name}" if multi_service else "Sentry"),
                    url=(
                        f"{sentry_base}/organizations/{quote(config.sentry_org, safe='')}/"
                        f"projects/{quote(service.sentry_project, safe='')}/"
                    ),
                )
            )

    unique_links: list[NotificationLink] = []
    seen: set[str] = set()
    for link in links:
        if link.url in seen:
            continue
        seen.add(link.url)
        unique_links.append(link)
    return unique_links


def _datadog_service_url(dd_site: str, service_name: str, environment: str) -> str:
    encoded_service = quote(service_name, safe="")
    encoded_env = quote(environment, safe="")
    return f"https://app.{dd_site}/apm/services/{encoded_service}?env={encoded_env}"


def _sentry_ui_base(config: ESSConfig) -> str:
    host = config.sentry_host.strip().rstrip("/")
    parsed = urlparse(host)
    if parsed.scheme:
        return host
    return f"https://{host}"


def _service_name_from_summary(summary: str) -> str | None:
    service_name, separator, _rest = summary.partition(": ")
    if not separator:
        return None
    return service_name.strip() or None


def _notification_context_label(config: ESSConfig, session: MonitoringSession) -> str | None:
    if resolve_teams_delivery_mode(config, session) != TeamsDeliveryMode.ALL:
        return None
    label = str(session.deploy.extra_context.get("notification_label") or "").strip()
    scenario = str(session.deploy.extra_context.get("notification_scenario") or "").strip()
    if label and scenario:
        return f"{label} • {scenario}"
    return label or scenario or None


def _build_card_facts(
    session: MonitoringSession,
    decision: NotificationDecision,
) -> list[dict[str, str]]:
    deploy = session.deploy.deployment
    facts = [
        {"title": "Environment", "value": deploy.environment.value},
        {"title": "Services", "value": _display_services(_decision_services(session, decision))},
        {"title": "Release", "value": _release_reference(session)},
        {"title": "Severity", "value": decision.overall_severity.value},
    ]
    if decision.cycle_number is not None and decision.kind != NotificationKind.SUMMARY:
        facts.append({"title": "Cycle", "value": str(decision.cycle_number)})
    if decision.kind == NotificationKind.SUMMARY:
        facts.append(
            {
                "title": "Checks",
                "value": f"{session.checks_completed}/{session.checks_planned}",
            }
        )
    return facts


def _release_reference(session: MonitoringSession) -> str:
    deploy = session.deploy.deployment
    if deploy.release_version:
        return f"release {deploy.release_version}"
    return f"commit {deploy.commit_sha[:12]}"


def _display_services(service_names: Iterable[str]) -> str:
    values = [service_name for service_name in service_names if service_name]
    if not values:
        return "none"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return ", ".join(values)
    return f"{values[0]}, +{len(values) - 1} more"


def _trim_multiline_text(value: str, max_lines: int) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines]) + "\n..."


def _unique_strings(values: Iterable[str | None]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _teams_color(severity: HealthSeverity) -> str:
    if severity == HealthSeverity.CRITICAL:
        return "Attention"
    if severity == HealthSeverity.WARNING:
        return "Warning"
    if severity == HealthSeverity.HEALTHY:
        return "Good"
    return "Default"


__all__ = [
    "NotificationDecision",
    "NotificationKind",
    "NotificationLink",
    "TeamsDeliveryMode",
    "TeamsDeliveryResult",
    "TeamsPublisher",
    "build_completion_warning_notification",
    "build_investigation_notification",
    "build_summary_notification",
    "build_teams_card",
    "evaluate_cycle_notification",
    "resolve_webhook_url",
    "resolve_teams_delivery_mode",
    "supports_thread_replies",
]
