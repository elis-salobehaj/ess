"""Phase 5 end-to-end coverage for deploy trigger to Teams delivery."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from src.config import ESSConfig
from src.main import create_app
from src.models import HealthCheckResult, HealthFinding, HealthSeverity
from src.notifications import TeamsDeliveryResult


async def test_mock_trigger_cycles_to_notification(valid_deploy_payload: dict) -> None:
    config = ESSConfig(
        _env_file=None,
        dd_api_key="test-dd-key",
        dd_app_key="test-dd-app-key",
        sentry_auth_token="test-sentry-token",
        teams_enabled=True,
        default_teams_webhook_url="https://outlook.office.com/webhook/test-phase-5",
    )
    app = create_app(config=config)

    payload = copy.deepcopy(valid_deploy_payload)
    payload["monitoring"]["window_minutes"] = 4
    payload["monitoring"]["check_interval_minutes"] = 2

    warning_one = HealthCheckResult(
        job_id="pending",
        cycle_number=1,
        checked_at=datetime.now(tz=UTC),
        overall_severity=HealthSeverity.WARNING,
        findings=[
            HealthFinding(
                tool="datadog.error_logs",
                severity=HealthSeverity.WARNING,
                summary="example-service: warning one",
            )
        ],
        services_checked=["hub-ca-auth"],
    )
    warning_two = HealthCheckResult(
        job_id="pending",
        cycle_number=2,
        checked_at=datetime.now(tz=UTC),
        overall_severity=HealthSeverity.WARNING,
        findings=[
            HealthFinding(
                tool="datadog.error_logs",
                severity=HealthSeverity.WARNING,
                summary="example-service: warning two",
            )
        ],
        services_checked=["hub-ca-auth"],
    )
    app.state.datadog_agent.run_health_check = AsyncMock(side_effect=[warning_one, warning_two])
    app.state.teams_publisher.post_card = AsyncMock(
        return_value=TeamsDeliveryResult(ok=True, status_code=200, response_text="1")
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/deploy", json=payload)
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        job = app.state.scheduler._scheduler.get_job(job_id)
        assert job is not None

        await app.state.scheduler._run_check(*job.args)
        await app.state.scheduler._run_check(*job.args)

        status_response = await client.get(f"/api/v1/deploy/{job_id}")
        assert status_response.status_code == 200
        body = status_response.json()
        assert body["status"] == "completed"
        assert body["checks_completed"] == 2
        assert body["latest_result"]["overall_severity"] == "WARNING"

        app.state.teams_publisher.post_card.assert_awaited_once()
        metrics_response = await client.get("/metrics")
        assert metrics_response.status_code == 200
        assert "ess_active_sessions 0" in metrics_response.text
        assert "ess_checks_executed_total 2" in metrics_response.text
        assert "ess_alerts_sent_total 1" in metrics_response.text
