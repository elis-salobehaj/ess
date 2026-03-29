"""Tests for Teams notification policy, cards, and delivery."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.config import ESSConfig
from src.models import DeployTrigger, HealthCheckResult, HealthFinding, HealthSeverity
from src.notifications import (
    TeamsPublisher,
    build_investigation_notification,
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


def _cfg() -> ESSConfig:
    return ESSConfig(
        _env_file=None,
        dd_api_key="k",
        dd_app_key="a",
        dd_site="datadoghq.com",
        sentry_auth_token="s",
        sentry_host="https://sentry.example.com",
        sentry_org="example",
        teams_delivery_mode="all",
    )


def _result(
    cycle_number: int,
    severity: HealthSeverity,
    findings: list[HealthFinding] | None = None,
) -> HealthCheckResult:
    return HealthCheckResult(
        job_id="ess-notify123",
        cycle_number=cycle_number,
        checked_at=datetime.now(tz=UTC),
        overall_severity=severity,
        findings=findings
        or [
            HealthFinding(
                tool="datadog.monitor_status",
                severity=severity,
                summary=f"example-service: cycle {cycle_number} severity {severity.value}",
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
        checks_planned=3,
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
        assert decision.recommendations

    def test_first_warning_does_not_notify(self) -> None:
        first = _result(1, HealthSeverity.WARNING)
        session = _session([first])

        decision, reason = evaluate_cycle_notification(session, first)

        assert decision is None
        assert reason == "warning_threshold_not_reached"

    def test_builds_investigation_follow_up_for_agent_summary(self) -> None:
        result = _result(
            2,
            HealthSeverity.WARNING,
            findings=[
                HealthFinding(
                    tool="agent.investigation_summary",
                    severity=HealthSeverity.WARNING,
                    summary="example-service: Severity: WARNING",
                    details=(
                        "Severity: WARNING\n"
                        "New Sentry release issues correlate with /auth failures."
                    ),
                ),
                HealthFinding(
                    tool="sentry.new_release_issues",
                    severity=HealthSeverity.WARNING,
                    summary="example-service: 2 new release issues detected",
                ),
            ],
        )
        session = _session([_result(1, HealthSeverity.WARNING), result])
        alert_decision, _reason = evaluate_cycle_notification(session, result)

        investigation = build_investigation_notification(session, result, alert_decision)

        assert investigation is not None
        assert investigation.kind.value == "investigation"
        assert investigation.related_notification_key == "ess-notify123:warning:2"
        assert investigation.investigation_summary is not None

    def test_summary_card_contains_timeline_and_links(self) -> None:
        session = _session(
            [
                _result(1, HealthSeverity.HEALTHY),
                _result(
                    2,
                    HealthSeverity.WARNING,
                    findings=[
                        HealthFinding(
                            tool="sentry.new_release_issues",
                            severity=HealthSeverity.WARNING,
                            summary="example-service: 1 new release issue detected",
                        )
                    ],
                ),
            ]
        )

        decision = build_summary_notification(session)
        card = build_teams_card(_cfg(), session, decision)

        assert decision.kind.value == "summary"
        assert decision.timeline_entries
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.2"
        assert card["actions"]
        action_titles = [action["title"] for action in card["actions"]]
        assert any(title.startswith("Datadog") for title in action_titles)
        assert any(title.startswith("Sentry") for title in action_titles)

    def test_card_renders_optional_context_label_in_all_mode(self) -> None:
        trigger = _load_example_trigger()
        payload = trigger.model_dump(mode="json")
        payload["extra_context"] = {
            "teams_mode": "all",
            "notification_label": "ESS Teams Scenario Test",
            "notification_scenario": "critical-investigation",
        }
        session = MonitoringSession(
            job_id="ess-notify123",
            deploy=DeployTrigger.model_validate(payload),
            started_at=datetime.now(tz=UTC),
            checks_completed=1,
            checks_planned=1,
            results=[_result(1, HealthSeverity.CRITICAL)],
        )

        decision, _reason = evaluate_cycle_notification(session, session.results[-1])
        card = build_teams_card(_cfg(), session, decision)

        assert card["body"][0]["text"] == "ESS Teams Scenario Test • critical-investigation"

    def test_card_hides_context_label_in_real_world_mode(self) -> None:
        trigger = _load_example_trigger()
        payload = trigger.model_dump(mode="json")
        payload["extra_context"] = {
            "teams_mode": "real-world",
            "notification_label": "ESS Teams Scenario Test",
            "notification_scenario": "critical-investigation",
        }
        session = MonitoringSession(
            job_id="ess-notify123",
            deploy=DeployTrigger.model_validate(payload),
            started_at=datetime.now(tz=UTC),
            checks_completed=1,
            checks_planned=1,
            results=[_result(1, HealthSeverity.CRITICAL)],
        )

        decision, _reason = evaluate_cycle_notification(session, session.results[-1])
        card = build_teams_card(
            ESSConfig(
                _env_file=None,
                dd_api_key="k",
                dd_app_key="a",
                dd_site="datadoghq.com",
                sentry_auth_token="s",
                sentry_host="https://sentry.example.com",
                sentry_org="example",
                teams_delivery_mode="real-world",
            ),
            session,
            decision,
        )

        assert card["body"][0]["text"] == "ESS detected a critical deploy issue"


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
        assert result.attempts == 1
        assert calls[0][2] == 7
        assert calls[0][1]["attachments"][0]["contentType"] == (
            "application/vnd.microsoft.card.adaptive"
        )

    async def test_retries_retryable_failures_and_then_succeeds(self) -> None:
        calls: list[int] = []
        sleeps: list[float] = []

        async def _transport(_webhook_url: str, _payload: dict, _timeout_seconds: int):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return 503, "temporary failure"
            return 200, "1"

        async def _sleep(seconds: float) -> None:
            sleeps.append(seconds)

        publisher = TeamsPublisher(
            transport=_transport,
            retry_attempts=3,
            retry_backoff_seconds=1.0,
            sleep=_sleep,
        )
        result = await publisher.post_card(
            "https://outlook.office.com/webhook/test",
            {"type": "AdaptiveCard", "version": "1.5", "body": []},
        )

        assert result.ok is True
        assert result.attempts == 2
        assert sleeps == [1.0]

    async def test_does_not_retry_non_retryable_http_failure(self) -> None:
        calls: list[int] = []
        sleeps: list[float] = []

        async def _transport(_webhook_url: str, _payload: dict, _timeout_seconds: int):
            calls.append(len(calls) + 1)
            return 400, "bad request"

        async def _sleep(seconds: float) -> None:
            sleeps.append(seconds)

        publisher = TeamsPublisher(
            transport=_transport,
            retry_attempts=3,
            sleep=_sleep,
        )
        result = await publisher.post_card(
            "https://outlook.office.com/webhook/test",
            {"type": "AdaptiveCard", "version": "1.5", "body": []},
        )

        assert result.ok is False
        assert result.status_code == 400
        assert result.attempts == 1
        assert sleeps == []
