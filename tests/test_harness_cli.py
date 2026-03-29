from __future__ import annotations

import asyncio
import json
from pathlib import Path

from typer.testing import CliRunner

from src.config import ESSConfig
from src.harness_cli import (
    DEFAULT_TIMEOUT_SECONDS,
    TIMEOUT_BUFFER_SECONDS,
    _build_teams_scenario_results,
    _run_teams_scenario_batch,
    app,
)

runner = CliRunner()


def test_root_command_prints_help() -> None:
    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Development harness commands for ESS." in result.output
    assert "live" in result.output
    assert "degraded" in result.output
    assert "install-completion" not in result.output
    assert "show-completion" not in result.output


def test_live_command_reports_missing_ess_with_helper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trigger = tmp_path / "trigger.json"
    trigger.write_text("{}", encoding="utf-8")

    async def fake_check_server_available(host: str, port: int) -> bool:
        del host, port
        return False

    monkeypatch.setattr("src.harness_cli._check_server_available", fake_check_server_available)

    result = runner.invoke(app, ["live", "--trigger", str(trigger)])

    assert result.exit_code == 1
    assert "ESS is not running at http://127.0.0.1:8080." in result.output
    assert "uv run uvicorn src.main:app --host 127.0.0.1 --port 8080 --reload" in result.output


def test_live_command_writes_status_and_summary_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trigger = tmp_path / "trigger.json"
    trace_path = tmp_path / "agent_trace.jsonl"
    trigger.write_text("{}", encoding="utf-8")

    async def fake_check_server_available(host: str, port: int) -> bool:
        return True

    async def fake_post_trigger_and_wait(
        host: str,
        port: int,
        trigger_path: Path,
        *,
        poll_interval_seconds: float,
        timeout_seconds: int,
    ) -> tuple[dict[str, object], dict[str, object]]:
        del host, port, trigger_path, poll_interval_seconds, timeout_seconds
        return (
            {"job_id": "ess-test1234"},
            {
                "job_id": "ess-test1234",
                "status": "completed",
                "checks_completed": 2,
                "checks_planned": 2,
                "latest_result": {
                    "overall_severity": "HEALTHY",
                    "findings": [
                        {
                            "tool": "agent.summary",
                            "summary": "Synthetic happy-path trigger harness result.",
                        }
                    ],
                },
            },
        )

    monkeypatch.setattr("src.harness_cli._check_server_available", fake_check_server_available)
    monkeypatch.setattr("src.harness_cli._post_trigger_and_wait", fake_post_trigger_and_wait)

    result = runner.invoke(
        app,
        [
            "live",
            "--trigger",
            str(trigger),
            "--trace-path",
            str(trace_path),
        ],
    )

    assert result.exit_code == 0
    assert "Expected session trace:" in result.output

    status_path = tmp_path / "agent_trace_status_ess-test1234.json"
    summary_path = tmp_path / "agent_trace_summary_ess-test1234.json"
    assert status_path.is_file()
    assert summary_path.is_file()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["job_id"] == "ess-test1234"
    assert summary["overall_severity"] == "HEALTHY"


def test_live_timeout_defaults_to_trigger_window_plus_buffer(tmp_path: Path) -> None:
    trigger = tmp_path / "trigger.json"
    trigger.write_text(
        json.dumps(
            {
                "monitoring": {
                    "window_minutes": 10,
                }
            }
        ),
        encoding="utf-8",
    )

    from src.harness_cli import _resolve_timeout_seconds

    timeout_seconds = _resolve_timeout_seconds(trigger, None)

    assert timeout_seconds == max(DEFAULT_TIMEOUT_SECONDS, 10 * 60 + TIMEOUT_BUFFER_SECONDS)


def test_build_teams_scenario_results_repeated_warning(tmp_path: Path) -> None:
    trigger = tmp_path / "trigger.json"
    trigger.write_text(
        json.dumps(
            {
                "deployment": {
                    "gitlab_pipeline_id": "123",
                    "gitlab_project": "group/repo",
                    "commit_sha": "abcdef1234567",
                    "release_version": "2.4.6",
                    "deployed_by": "jane.doe",
                    "deployed_at": "2026-03-29T12:00:00Z",
                    "environment": "qa",
                    "regions": ["ca"],
                },
                "services": [
                    {
                        "name": "example-service",
                        "datadog_service_name": "example-service",
                    }
                ],
                "monitoring": {
                    "window_minutes": 5,
                    "check_interval_minutes": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    from src.harness_cli import _load_trigger_payload

    results = _build_teams_scenario_results(
        _load_trigger_payload(trigger),
        job_id="ess-scenario-test",
        scenario_name="repeated-warning",
    )

    assert len(results) == 2
    assert results[0].overall_severity == "WARNING"
    assert results[1].overall_severity == "WARNING"


def test_run_teams_scenario_batch_posts_expected_cards(tmp_path: Path, monkeypatch) -> None:
    trigger = tmp_path / "trigger.json"
    trace_path = tmp_path / "teams_scenarios_agent_trace.jsonl"
    trigger.write_text(
        json.dumps(
            {
                "deployment": {
                    "gitlab_pipeline_id": "123",
                    "gitlab_project": "group/repo",
                    "commit_sha": "abcdef1234567",
                    "release_version": "2.4.6",
                    "deployed_by": "jane.doe",
                    "deployed_at": "2026-03-29T12:00:00Z",
                    "environment": "qa",
                    "regions": ["ca"],
                },
                "services": [
                    {
                        "name": "example-service",
                        "datadog_service_name": "example-service",
                        "sentry_project": "example-service",
                        "sentry_project_id": 7,
                    }
                ],
                "monitoring": {
                    "window_minutes": 5,
                    "check_interval_minutes": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    posted_cards: list[dict[str, object]] = []

    async def fake_post_card(self, webhook_url: str, card: dict[str, object]):
        del self
        posted_cards.append({"webhook_url": webhook_url, "card": card})

        from src.notifications import TeamsDeliveryResult

        return TeamsDeliveryResult(ok=True, status_code=200, response_text="1", attempts=1)

    monkeypatch.setattr("src.harness_cli.TeamsPublisher.post_card", fake_post_card)

    cfg = ESSConfig(
        _env_file=None,
        dd_api_key="k",
        dd_app_key="a",
        sentry_auth_token="s",
        sentry_host="https://sentry.example.com",
        sentry_org="example",
        teams_enabled=True,
        default_teams_webhook_url="https://outlook.office.com/webhook/test",
        debug_trace_enabled=True,
        agent_trace_path=trace_path,
    )

    summaries = asyncio.run(
        _run_teams_scenario_batch(
            cfg,
            trigger_path=trigger,
            scenarios=["healthy-summary", "repeated-warning", "critical-investigation"],
            label="ESS Teams Scenario Test",
            teams_mode="all",
            inter_scenario_delay_seconds=0,
        )
    )

    headlines = [item["card"]["body"][1]["text"] for item in posted_cards]
    assert len(summaries) == 3
    assert len(posted_cards) == 6
    assert "ESS monitoring window complete" in headlines
    assert "ESS observed repeated deploy warnings" in headlines
    assert "ESS investigation follow-up" in headlines


def test_run_teams_scenario_batch_real_world_posts_only_operational_cards(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trigger = tmp_path / "trigger.json"
    trace_path = tmp_path / "teams_scenarios_agent_trace.jsonl"
    trigger.write_text(
        json.dumps(
            {
                "deployment": {
                    "gitlab_pipeline_id": "123",
                    "gitlab_project": "group/repo",
                    "commit_sha": "abcdef1234567",
                    "release_version": "2.4.6",
                    "deployed_by": "jane.doe",
                    "deployed_at": "2026-03-29T12:00:00Z",
                    "environment": "qa",
                    "regions": ["ca"],
                },
                "services": [
                    {
                        "name": "example-service",
                        "datadog_service_name": "example-service",
                        "sentry_project": "example-service",
                        "sentry_project_id": 7,
                    }
                ],
                "monitoring": {
                    "window_minutes": 5,
                    "check_interval_minutes": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    posted_cards: list[dict[str, object]] = []

    async def fake_post_card(self, webhook_url: str, card: dict[str, object]):
        del self
        posted_cards.append({"webhook_url": webhook_url, "card": card})

        from src.notifications import TeamsDeliveryResult

        return TeamsDeliveryResult(ok=True, status_code=200, response_text="1", attempts=1)

    monkeypatch.setattr("src.harness_cli.TeamsPublisher.post_card", fake_post_card)

    cfg = ESSConfig(
        _env_file=None,
        dd_api_key="k",
        dd_app_key="a",
        sentry_auth_token="s",
        sentry_host="https://sentry.example.com",
        sentry_org="example",
        teams_enabled=True,
        teams_delivery_mode="real-world",
        default_teams_webhook_url="https://outlook.office.com/webhook/test",
        debug_trace_enabled=True,
        agent_trace_path=trace_path,
    )

    asyncio.run(
        _run_teams_scenario_batch(
            cfg,
            trigger_path=trigger,
            scenarios=["healthy-summary", "repeated-warning", "critical-investigation"],
            label="ESS Teams Scenario Test",
            teams_mode="real-world",
            inter_scenario_delay_seconds=0,
        )
    )

    headlines = [item["card"]["body"][0]["text"] for item in posted_cards]
    assert headlines == [
        "ESS observed repeated deploy warnings",
        "ESS detected a critical deploy issue",
    ]
