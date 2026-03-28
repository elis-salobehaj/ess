"""MS Teams notification policy, card building, and delivery.

Phase 1.5 keeps this intentionally small: one bounded webhook publisher,
minimal Datadog-only card content, and simple policy helpers for warning,
critical, and summary notifications.
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Any, Literal

import aiohttp
import structlog
from pydantic import BaseModel, ConfigDict, Field

from src.models import HealthCheckResult, HealthSeverity
from src.scheduler import MonitoringSession

logger: structlog.BoundLogger = structlog.get_logger(__name__)  # type: ignore[assignment]

_TEAMS_RESPONSE_PREVIEW_LIMIT = 500


class NotificationKind(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"
    SUMMARY = "summary"


class NotificationDecision(BaseModel):
    """A policy decision to notify Teams."""

    model_config = ConfigDict(extra="forbid")

    kind: NotificationKind
    headline: str
    summary: str
    overall_severity: HealthSeverity
    reason: str
    cycle_number: int | None = None
    finding_summaries: list[str] = Field(default_factory=list)


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


class TeamsPublisher:
    """Bounded async Teams webhook publisher."""

    def __init__(
        self,
        *,
        timeout_seconds: int = 10,
        transport=None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._transport = transport

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

        try:
            status_code, response_text = await self._post_payload(webhook_url, payload)
        except Exception as exc:
            logger.warning(
                "teams_delivery_exception",
                error=str(exc),
                timeout_seconds=self._timeout_seconds,
            )
            return TeamsDeliveryResult(ok=False, error=str(exc))

        ok = 200 <= status_code < 300
        response_preview = response_text[:_TEAMS_RESPONSE_PREVIEW_LIMIT]
        if ok:
            logger.info("teams_delivery_ok", status_code=status_code)
            return TeamsDeliveryResult(
                ok=True,
                status_code=status_code,
                response_text=response_preview,
            )

        logger.warning(
            "teams_delivery_failed",
            status_code=status_code,
            response_text=response_preview,
        )
        return TeamsDeliveryResult(
            ok=False,
            status_code=status_code,
            response_text=response_preview,
            error=response_preview or f"Teams webhook returned HTTP {status_code}",
        )

    async def _post_payload(
        self,
        webhook_url: str,
        payload: dict[str, Any],
    ) -> tuple[int, str]:
        if self._transport is not None:
            return await self._transport(webhook_url, payload, self._timeout_seconds)

        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
            webhook_url,
            json=payload,
        ) as response:
            return response.status, await response.text()


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


def evaluate_cycle_notification(
    session: MonitoringSession,
    result: HealthCheckResult,
) -> tuple[NotificationDecision | None, str]:
    if result.overall_severity == HealthSeverity.CRITICAL:
        return (
            NotificationDecision(
                kind=NotificationKind.CRITICAL,
                headline="ESS detected a critical Datadog signal",
                summary=_build_cycle_summary(result),
                overall_severity=result.overall_severity,
                reason="critical_immediate",
                cycle_number=result.cycle_number,
                finding_summaries=_finding_summaries(result),
            ),
            "critical_immediate",
        )

    if result.overall_severity == HealthSeverity.WARNING:
        streak = _warning_streak(session.results)
        if streak == 2:
            return (
                NotificationDecision(
                    kind=NotificationKind.WARNING,
                    headline="ESS observed repeated Datadog warnings",
                    summary=_build_cycle_summary(result),
                    overall_severity=result.overall_severity,
                    reason="warning_repeated",
                    cycle_number=result.cycle_number,
                    finding_summaries=_finding_summaries(result),
                ),
                "warning_repeated",
            )
        if streak < 2:
            return None, "warning_threshold_not_reached"
        return None, "warning_already_notified_for_streak"

    return None, "no_notification_required"


def build_summary_notification(session: MonitoringSession) -> NotificationDecision:
    counts = Counter(result.overall_severity.value for result in session.results)
    overall = _aggregate_severity(session.results)
    summary = (
        "Monitoring window completed. "
        f"Overall severity: {overall.value}. "
        f"Checks completed: {session.checks_completed}/{session.checks_planned}. "
        f"Healthy={counts.get(HealthSeverity.HEALTHY.value, 0)}, "
        f"Warning={counts.get(HealthSeverity.WARNING.value, 0)}, "
        f"Critical={counts.get(HealthSeverity.CRITICAL.value, 0)}, "
        f"Unknown={counts.get(HealthSeverity.UNKNOWN.value, 0)}."
    )
    latest_findings = _finding_summaries(session.results[-1]) if session.results else []
    return NotificationDecision(
        kind=NotificationKind.SUMMARY,
        headline="ESS monitoring window complete",
        summary=summary,
        overall_severity=overall,
        reason="monitoring_window_complete",
        cycle_number=session.checks_completed or None,
        finding_summaries=latest_findings,
    )


def build_teams_card(
    session: MonitoringSession,
    decision: NotificationDecision,
) -> dict[str, Any]:
    deploy = session.deploy.deployment
    services = ", ".join(service.name for service in session.deploy.services)
    facts = [
        {"title": "Environment", "value": deploy.environment.value},
        {"title": "Services", "value": services},
        {"title": "Commit", "value": deploy.commit_sha[:12]},
        {"title": "Checks", "value": f"{session.checks_completed}/{session.checks_planned}"},
        {"title": "Severity", "value": decision.overall_severity.value},
        {"title": "Job", "value": session.job_id},
    ]
    if decision.cycle_number is not None:
        facts.append({"title": "Cycle", "value": str(decision.cycle_number)})

    body: list[dict[str, Any]] = [
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
        {
            "type": "FactSet",
            "facts": facts,
        },
    ]

    if decision.finding_summaries:
        body.append(
            {
                "type": "TextBlock",
                "text": "Key findings:\n- " + "\n- ".join(decision.finding_summaries[:5]),
                "wrap": True,
            }
        )

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "msteams": {"width": "Full"},
        "body": body,
    }


def _build_cycle_summary(result: HealthCheckResult) -> str:
    return (
        f"Cycle {result.cycle_number} completed with severity {result.overall_severity.value}. "
        f"Services checked: {', '.join(result.services_checked) or 'none'}. "
        f"Findings: {len(result.findings)}."
    )


def _finding_summaries(result: HealthCheckResult) -> list[str]:
    return [finding.summary for finding in result.findings[:5]]


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
    "TeamsDeliveryResult",
    "TeamsPublisher",
    "build_summary_notification",
    "build_teams_card",
    "evaluate_cycle_notification",
    "resolve_webhook_url",
]
