from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from src.harness_cli import DEFAULT_TIMEOUT_SECONDS, TIMEOUT_BUFFER_SECONDS, app

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
