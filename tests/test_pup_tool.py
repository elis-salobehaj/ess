"""Unit tests for PupTool (D1.2–D1.4) and pup_to_tool_result normaliser (D1.5).

All Pup subprocess calls are mocked — no real Datadog connection is made.
Integration tests against a live Datadog account are in the D2.5 section and
must be run with ``uv run pytest -m integration``.

Mocking strategy
----------------
- ``asyncio.create_subprocess_exec`` is replaced with ``AsyncMock`` that returns
  a fake ``proc`` object.
- ``asyncio.wait_for`` is patched only in timeout tests so the test does not
  actually wait.
- Convenience methods (D1.3 / D1.4) are tested by replacing ``tool.execute``
  with an ``AsyncMock`` and asserting the argument list — this keeps the tests
  fast and independent of subprocess machinery.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import ESSConfig
from src.metrics import ESSMetrics
from src.models import ToolResult
from src.tools.normalise import pup_to_tool_result
from src.tools.pup_tool import PupResult, PupTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg() -> ESSConfig:
    """Minimal ESSConfig for unit tests — no real credentials needed."""
    return ESSConfig(
        _env_file=None,
        dd_api_key="test-api-key",
        dd_app_key="test-app-key",
        dd_site="datadoghq.com",
        sentry_auth_token="test-sentry-token",
        pup_max_concurrent=5,
        pup_default_timeout=30,
    )


def _mock_proc(
    stdout: bytes,
    stderr: bytes = b"",
    returncode: int = 0,
) -> MagicMock:
    """Return a mock asyncio.Process with preset stdout/stderr."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


def _ok_result() -> PupResult:
    """A generic successful PupResult for use in convenience-method tests."""
    return PupResult(command="pup monitors list", exit_code=0, data={}, stderr="", duration_ms=5)


# ---------------------------------------------------------------------------
# D1.2 — PupTool.execute() core behaviour
# ---------------------------------------------------------------------------


class TestPupToolExecuteSuccess:
    async def test_returns_parsed_json_dict(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)
        payload = {"monitors": [{"id": 1, "status": "OK"}]}

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(json.dumps(payload).encode())
            result = await tool.execute(["monitors", "list"])

        assert result.exit_code == 0
        assert result.data == payload
        assert result.stderr == ""
        assert result.duration_ms >= 0

    async def test_resets_consecutive_failures_on_success(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)
        tool._consecutive_failures = 2  # prior failures

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(b'{"ok": true}')
            await tool.execute(["monitors", "list"])

        assert tool._consecutive_failures == 0

    async def test_non_json_stdout_wrapped_as_raw_output(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)
        raw_text = b"some plain text output"

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(raw_text)
            result = await tool.execute(["monitors", "list"])

        assert result.exit_code == 0
        assert isinstance(result.data, dict)
        assert "raw_output" in result.data
        assert result.data["raw_output"] == raw_text.decode()

    async def test_command_string_in_result(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(b"{}")
            result = await tool.execute(["monitors", "list", "--tags=env:prod"])

        assert "pup" in result.command
        assert "monitors" in result.command
        assert "list" in result.command

    async def test_passes_dd_env_vars_to_subprocess(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(b"{}")
            await tool.execute(["monitors", "list"])

        call_kwargs = mock_exec.call_args
        env_passed = call_kwargs.kwargs.get("env") or {}
        assert env_passed.get("DD_API_KEY") == "test-api-key"
        assert env_passed.get("DD_APP_KEY") == "test-app-key"
        assert env_passed.get("DD_SITE") == "datadoghq.com"
        assert env_passed.get("FORCE_AGENT_MODE") == "1"

    async def test_output_json_flag_always_appended(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(b"{}")
            await tool.execute(["monitors", "list"])

        positional_args = mock_exec.call_args.args
        assert "--output" in positional_args
        assert "json" in positional_args

    async def test_records_metrics_for_successful_calls(self) -> None:
        cfg = _cfg()
        metrics = ESSMetrics()
        tool = PupTool(cfg, metrics=metrics)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(b"{}")
            await tool.execute(["monitors", "list"])

        rendered = metrics.render_prometheus()
        assert 'ess_tool_calls_total{tool="datadog.pup"} 1' in rendered


class TestPupToolExecuteFailurePaths:
    async def test_non_zero_exit_returns_error_result(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(b"", b"auth failed", returncode=1)
            result = await tool.execute(["monitors", "list"])

        assert result.exit_code == 1
        assert result.data is None
        assert "auth failed" in result.stderr
        assert tool._consecutive_failures == 1

    async def test_pup_not_found_records_failure(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = FileNotFoundError("pup: not found")
            result = await tool.execute(["monitors", "list"])

        assert result.exit_code == -1
        assert "not found" in result.stderr
        assert tool._consecutive_failures == 1

    async def test_timeout_kills_process_and_records_failure(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)
        mock_proc = _mock_proc(b"", b"")  # communicate called again after kill

        async def _fake_wait_for(coro, timeout=None):
            # Close the coroutine so it is not left unawaited (avoids RuntimeWarning).
            coro.close()
            raise TimeoutError()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            with patch("asyncio.wait_for", new=_fake_wait_for):
                result = await tool.execute(["monitors", "list"], timeout=1)

        assert result.exit_code == -1
        assert "Timed out" in result.stderr
        assert tool._consecutive_failures == 1
        mock_proc.kill.assert_called_once()


class TestCircuitBreaker:
    async def test_circuit_opens_after_three_consecutive_failures(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(b"", b"err", returncode=1)
            for _ in range(3):
                await tool.execute(["monitors", "list"])

        assert tool._circuit_open is True
        assert tool._consecutive_failures == 3

    async def test_circuit_open_returns_immediately_without_subprocess(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)
        tool._circuit_open = True

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            result = await tool.execute(["monitors", "list"])

        mock_exec.assert_not_called()
        assert result.exit_code == -1
        assert "Circuit breaker" in result.stderr
        assert result.duration_ms == 0

    async def test_success_does_not_reopen_after_below_threshold(self) -> None:
        cfg = _cfg()
        tool = PupTool(cfg)
        tool._consecutive_failures = 2  # one below threshold

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(b"{}")
            await tool.execute(["monitors", "list"])

        assert tool._circuit_open is False
        assert tool._consecutive_failures == 0


# ---------------------------------------------------------------------------
# D1.3 — Triage convenience methods
# ---------------------------------------------------------------------------


class TestTriageMethods:
    async def test_get_monitor_status_args(self) -> None:
        tool = PupTool(_cfg())
        tool.execute = AsyncMock(return_value=_ok_result())

        await tool.get_monitor_status("my-svc", "production")

        tool.execute.assert_called_once_with(
            [
                "monitors",
                "list",
                "--tags=service:my-svc,env:production",
            ]
        )

    async def test_search_error_logs_default_minutes(self) -> None:
        tool = PupTool(_cfg())
        tool.execute = AsyncMock(return_value=_ok_result())

        await tool.search_error_logs("my-svc")

        tool.execute.assert_called_once_with(
            [
                "logs",
                "search",
                "--query=service:my-svc status:error",
                "--from=10m",
            ]
        )

    async def test_search_error_logs_custom_minutes(self) -> None:
        tool = PupTool(_cfg())
        tool.execute = AsyncMock(return_value=_ok_result())

        await tool.search_error_logs("my-svc", minutes=30)

        tool.execute.assert_called_once_with(
            [
                "logs",
                "search",
                "--query=service:my-svc status:error",
                "--from=30m",
            ]
        )

    async def test_get_apm_stats_args(self) -> None:
        # pup apm services stats has no --service flag; lists all services in env
        tool = PupTool(_cfg())
        tool.execute = AsyncMock(return_value=_ok_result())

        await tool.get_apm_stats("my-svc", "production")

        tool.execute.assert_called_once_with(
            [
                "apm",
                "services",
                "stats",
                "--env=production",
            ]
        )


# ---------------------------------------------------------------------------
# D1.4 — Investigation convenience methods
# ---------------------------------------------------------------------------


class TestInvestigationMethods:
    async def test_get_recent_incidents_args(self) -> None:
        tool = PupTool(_cfg())
        tool.execute = AsyncMock(return_value=_ok_result())

        await tool.get_recent_incidents()

        tool.execute.assert_called_once_with(["incidents", "list"])

    async def test_get_infrastructure_health_args(self) -> None:
        tool = PupTool(_cfg())
        tool.execute = AsyncMock(return_value=_ok_result())

        await tool.get_infrastructure_health("my-svc")

        tool.execute.assert_called_once_with(
            [
                "infrastructure",
                "hosts",
                "list",
                "--filter=service:my-svc",
            ]
        )

    async def test_get_apm_operations_args(self) -> None:
        tool = PupTool(_cfg())
        tool.execute = AsyncMock(return_value=_ok_result())

        await tool.get_apm_operations("my-svc", "production")

        tool.execute.assert_called_once_with(
            [
                "apm",
                "services",
                "operations",
                "--service=my-svc",
                "--env=production",
            ]
        )

    async def test_search_warning_logs_default_minutes(self) -> None:
        tool = PupTool(_cfg())
        tool.execute = AsyncMock(return_value=_ok_result())

        await tool.search_warning_logs("my-svc")

        tool.execute.assert_called_once_with(
            [
                "logs",
                "search",
                "--query=service:my-svc status:warn",
                "--from=10m",
            ]
        )

    async def test_search_warning_logs_custom_minutes(self) -> None:
        tool = PupTool(_cfg())
        tool.execute = AsyncMock(return_value=_ok_result())

        await tool.search_warning_logs("my-svc", minutes=30)

        tool.execute.assert_called_once_with(
            [
                "logs",
                "search",
                "--query=service:my-svc status:warn",
                "--from=30m",
            ]
        )

    async def test_get_apm_resources_args(self) -> None:
        tool = PupTool(_cfg())
        tool.execute = AsyncMock(return_value=_ok_result())

        await tool.get_apm_resources("my-svc", "http.request", "production")

        tool.execute.assert_called_once_with(
            [
                "apm",
                "services",
                "resources",
                "--service=my-svc",
                "--operation=http.request",
                "--env=production",
            ]
        )


# ---------------------------------------------------------------------------
# D1.5 — pup_to_tool_result normalisation
# ---------------------------------------------------------------------------


class TestPupToToolResult:
    def test_success_with_dict_data(self) -> None:
        pup = PupResult(
            command="pup monitors list",
            exit_code=0,
            data={"monitors": []},
            stderr="",
            duration_ms=42,
        )
        result = pup_to_tool_result(pup, "monitor_status")

        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.tool == "datadog.monitor_status"
        assert result.data == {"monitors": []}
        assert result.error is None
        assert result.duration_ms == 42

    def test_success_with_list_data_wrapped_as_items(self) -> None:
        pup = PupResult(
            command="pup incidents list",
            exit_code=0,
            data=[{"id": "INC-1"}, {"id": "INC-2"}],
            stderr="",
            duration_ms=10,
        )
        result = pup_to_tool_result(pup, "incidents")

        assert result.success is True
        assert isinstance(result.data, dict)
        assert result.data == {"items": [{"id": "INC-1"}, {"id": "INC-2"}]}

    def test_success_extracts_top_level_summary(self) -> None:
        pup = PupResult(
            command="pup monitors list",
            exit_code=0,
            data={"summary": "2 monitors alerting", "monitors": []},
            stderr="",
            duration_ms=5,
        )
        result = pup_to_tool_result(pup, "monitor_status")

        assert result.summary == "2 monitors alerting"

    def test_success_extracts_metadata_description(self) -> None:
        pup = PupResult(
            command="pup apm services stats svc",
            exit_code=0,
            data={"metadata": {"description": "APM stats for svc"}, "stats": {}},
            stderr="",
            duration_ms=5,
        )
        result = pup_to_tool_result(pup, "apm_stats")

        assert result.summary == "APM stats for svc"

    def test_success_fallback_summary_when_no_metadata(self) -> None:
        pup = PupResult(
            command="pup logs search",
            exit_code=0,
            data={"logs": []},
            stderr="",
            duration_ms=5,
        )
        result = pup_to_tool_result(pup, "error_logs")

        assert "error_logs" in result.summary

    def test_failure_non_zero_exit(self) -> None:
        pup = PupResult(
            command="pup monitors list",
            exit_code=1,
            data=None,
            stderr="authentication failed",
            duration_ms=20,
        )
        result = pup_to_tool_result(pup, "monitor_status")

        assert result.success is False
        assert result.data == {}
        assert result.error == "authentication failed"
        assert "authentication failed" in result.summary

    def test_failure_none_data_despite_zero_exit(self) -> None:
        # exit_code=0 but data=None (shouldn't happen normally, but be defensive)
        pup = PupResult(
            command="pup monitors list",
            exit_code=0,
            data=None,
            stderr="",
            duration_ms=1,
        )
        result = pup_to_tool_result(pup, "monitor_status")

        assert result.success is False

    def test_tool_name_is_dot_prefixed(self) -> None:
        pup = PupResult("cmd", 0, {}, "", 1)
        assert pup_to_tool_result(pup, "apm_stats").tool == "datadog.apm_stats"

    def test_raw_field_contains_command(self) -> None:
        pup = PupResult("pup monitors list", 0, {}, "", 1)
        result = pup_to_tool_result(pup, "monitor_status")
        assert result.raw["command"] == "pup monitors list"

    def test_failure_raw_field_contains_stderr(self) -> None:
        pup = PupResult("pup monitors list", 1, None, "error text", 1)
        result = pup_to_tool_result(pup, "monitor_status")
        assert result.raw["stderr"] == "error text"


# ---------------------------------------------------------------------------
# D2.5 — Integration tests (skipped unless -m integration)
# ---------------------------------------------------------------------------
# These tests call real Datadog — they require DD_API_KEY and DD_APP_KEY to be
# set in config/.env.  Run with: uv run pytest -m integration


@pytest.mark.integration
async def test_pup_monitors_list_real() -> None:
    """Call Pup monitors list against real Datadog — requires DD_API_KEY."""
    tool = PupTool(config=ESSConfig())
    result = await tool.get_monitor_status("example-auth-service", "production")
    assert result.exit_code == 0
    assert result.data is not None


@pytest.mark.integration
async def test_pup_apm_stats_real() -> None:
    """Call Pup APM stats against real Datadog."""
    tool = PupTool(config=ESSConfig())
    result = await tool.get_apm_stats("example-auth-service", "production")
    assert result.exit_code == 0


@pytest.mark.integration
async def test_pup_logs_search_real() -> None:
    """Call Pup logs search against real Datadog."""
    tool = PupTool(config=ESSConfig())
    result = await tool.search_error_logs("example-auth-service", minutes=30)
    assert result.exit_code == 0
