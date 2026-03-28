"""Tests for Teams notification policy and delivery."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.models import DeployTrigger, HealthCheckResult, HealthFinding, HealthSeverity
from src.notifications import (
    TeamsPublisher,
    build_summary_notification,
    build_teams_card,
    evaluate_cycle_notification,
)
from src.scheduler import MonitoringSession

_EXAMPLE_TRIGGER_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "examples"
    / "triggers"
    / "example-service-e2e.json"
)


def _load_example_trigger() -> DeployTrigger:
    return DeployTrigger.model_validate_json(_EXAMPLE_TRIGGER_PATH.read_text())


def _result(cycle_number: int, severity: HealthSeverity) -> HealthCheckResult:
    return HealthCheckResult(
        job_id="ess-notify123",
        cycle_number=cycle_number,
        checked_at=datetime.now(tz=UTC),
        overall_severity=severity,
        findings=[
            HealthFinding(
                tool="datadog.monitor_status",
                severity=severity,
                summary=f"cycle {cycle_number} severity {severity.value}",
            )
        ],
        services_checked=["example-service"],
    )


def _session(results: list[HealthCheckResult]) -> MonitoringSession:
    return MonitoringSession(
        job_id="ess-notify123",
        deploy=_load_example_trigger(),
        started_at=datetime.now(tz=UTC),
        checks_completed=len(results),
        checks_planned=2,
        results=results,
    )


class TestNotificationPolicy:
    def test_second_consecutive_warning_triggers_notification(self) -> None:
        first = _result(1, HealthSeverity.WARNING)
        second = _result(2, HealthSeverity.WARNING)
        session = _session([first, second])

        decision, reason = evaluate_cycle_notification(session, second)

        assert decision is not None
        assert decision.kind.value == "warning"
        assert reason == "warning_repeated"

    def test_first_warning_does_not_notify(self) -> None:
        first = _result(1, HealthSeverity.WARNING)
        session = _session([first])

        decision, reason = evaluate_cycle_notification(session, first)

        assert decision is None
        assert reason == "warning_threshold_not_reached"

    def test_critical_notifies_immediately(self) -> None:
        critical = _result(1, HealthSeverity.CRITICAL)
        session = _session([critical])

        decision, reason = evaluate_cycle_notification(session, critical)

        assert decision is not None
        assert decision.kind.value == "critical"
        assert reason == "critical_immediate"

    def test_summary_aggregates_all_cycles(self) -> None:
        session = _session(
            [
                _result(1, HealthSeverity.HEALTHY),
                _result(2, HealthSeverity.WARNING),
            ]
        )

        decision = build_summary_notification(session)
        card = build_teams_card(session, decision)

        assert decision.kind.value == "summary"
        assert "Healthy=1" in decision.summary
        assert card["type"] == "AdaptiveCard"


class TestTeamsPublisher:
    async def test_transport_success_is_reported(self) -> None:
        calls: list[tuple[str, dict, int]] = []

        async def _transport(webhook_url: str, payload: dict, timeout_seconds: int):
            calls.append((webhook_url, payload, timeout_seconds))
            return 200, "1"

        publisher = TeamsPublisher(timeout_seconds=7, transport=_transport)
        result = await publisher.post_card(
            "https://outlook.office.com/webhook/test",
            {"type": "AdaptiveCard", "version": "1.5", "body": []},
        )

        assert result.ok is True
        assert calls[0][2] == 7
        assert calls[0][1]["attachments"][0]["contentType"] == (
            "application/vnd.microsoft.card.adaptive"
        )

    async def test_transport_failure_is_reported(self) -> None:
        async def _transport(_webhook_url: str, _payload: dict, _timeout_seconds: int):
            raise TimeoutError("timed out")

        publisher = TeamsPublisher(transport=_transport)
        result = await publisher.post_card(
            "https://outlook.office.com/webhook/test",
            {"type": "AdaptiveCard", "version": "1.5", "body": []},
        )

        assert result.ok is False
        assert result.error == "timed out"
