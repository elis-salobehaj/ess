"""Typer-based development harness commands for ESS."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiohttp
import typer
import uvicorn

from src.config import ESSConfig
from src.main import create_app
from src.tools.pup_tool import PupResult

app = typer.Typer(
    help="Development harness commands for ESS.",
    pretty_exceptions_show_locals=False,
    add_completion=False,
)

DEFAULT_LIVE_HOST = "127.0.0.1"
DEFAULT_LIVE_PORT = 8080
DEFAULT_DEGRADED_HOST = "127.0.0.1"
DEFAULT_DEGRADED_PORT = 8011
DEFAULT_LIVE_TRACE_PATH = Path("_local_observability/agent_trace.jsonl")
DEFAULT_DEGRADED_TRACE_PATH = Path("_local_observability/degraded_harness_agent_trace.jsonl")
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 360
TIMEOUT_BUFFER_SECONDS = 180


@app.callback(invoke_without_command=True)
def _root_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)


def _pup_result(
    command: str,
    data: dict[str, Any] | list[Any],
    *,
    duration_ms: int = 5,
) -> PupResult:
    return PupResult(
        command=command,
        exit_code=0,
        data=data,
        stderr="",
        duration_ms=duration_ms,
    )


def _install_degraded_datadog_harness(app_instance: Any) -> None:
    async def fake_monitor_status(service: str, env: str) -> PupResult:
        return _pup_result(
            f"fake monitor_status {service} {env}",
            {
                "summary": "Synthetic healthy monitor status for degraded harness",
                "items": [],
            },
        )

    async def fake_error_logs(service: str, minutes: int = 10) -> PupResult:
        return _pup_result(
            f"fake error_logs {service} {minutes}",
            {
                "status": "success",
                "data": [
                    {
                        "service": service,
                        "status": "error",
                        "message": "Synthetic degraded-path validation error",
                        "timestamp": "2026-03-29T15:30:00Z",
                    }
                ],
                "metadata": {
                    "count": 1,
                    "command": "logs search",
                    "description": "Injected degraded-path validation error",
                },
            },
        )

    async def fake_apm_stats(service: str, env: str) -> PupResult:
        return _pup_result(
            f"fake apm_stats {service} {env}",
            {
                "summary": "Synthetic healthy APM stats for degraded harness",
                "data": {
                    "attributes": {
                        "services_stats": [
                            {
                                "service": service,
                                "operation": "servlet.request",
                                "requestPerSecond": 9.29,
                                "latencyAvg": 11983842.28,
                                "latencyP50": 6091216.78,
                                "latencyP99": 86320210.46,
                                "hits": "33438",
                                "apdex": {
                                    "score": 0.9998,
                                    "satisfied": "33423",
                                    "tolerating": "15",
                                },
                            }
                        ]
                    }
                },
            },
        )

    async def fake_incidents() -> PupResult:
        return _pup_result(
            "fake incidents",
            {
                "summary": "Synthetic healthy incidents result for degraded harness",
                "items": [],
            },
        )

    async def fake_infrastructure_health(service: str) -> PupResult:
        return _pup_result(
            f"fake infrastructure_health {service}",
            {
                "summary": "Synthetic healthy infrastructure health for degraded harness",
                "items": [],
            },
        )

    async def fake_apm_operations(service: str, env: str) -> PupResult:
        return _pup_result(
            f"fake apm_operations {service} {env}",
            {
                "summary": "Synthetic healthy APM operation breakdown for degraded harness",
                "items": [
                    {
                        "service": service,
                        "operation": "servlet.request",
                        "requestPerSecond": 9.29,
                        "latencyP99": 86320210.46,
                        "errorRate": 0.0,
                    }
                ],
            },
        )

    pup_tool = app_instance.state.datadog_agent._pup_tool
    pup_tool.get_monitor_status = fake_monitor_status
    pup_tool.search_error_logs = fake_error_logs
    pup_tool.get_apm_stats = fake_apm_stats
    pup_tool.get_recent_incidents = fake_incidents
    pup_tool.get_infrastructure_health = fake_infrastructure_health
    pup_tool.get_apm_operations = fake_apm_operations


def _artifact_paths(trace_path: Path, job_id: str) -> tuple[Path, Path, Path, Path]:
    stem = trace_path.stem
    parent = trace_path.parent
    session_trace = parent / f"{stem}_{job_id}.jsonl"
    digest = parent / f"{stem}_digest_{job_id}.md"
    status = parent / f"{stem}_status_{job_id}.json"
    summary = parent / f"{stem}_summary_{job_id}.json"
    return session_trace, digest, status, summary


def _build_summary(final_status: dict[str, Any]) -> dict[str, Any]:
    latest_result = final_status.get("latest_result") or {}
    findings = latest_result.get("findings") or []
    return {
        "job_id": final_status.get("job_id"),
        "status": final_status.get("status"),
        "checks_completed": final_status.get("checks_completed"),
        "checks_planned": final_status.get("checks_planned"),
        "overall_severity": latest_result.get("overall_severity"),
        "tools": [finding.get("tool") for finding in findings],
        "summary": findings[0].get("summary") if findings else None,
    }


def _resolve_timeout_seconds(trigger_path: Path, explicit_timeout_seconds: int | None) -> int:
    if explicit_timeout_seconds is not None:
        return explicit_timeout_seconds

    trigger_payload = json.loads(trigger_path.read_text(encoding="utf-8"))
    monitoring = trigger_payload.get("monitoring") or {}
    window_minutes = monitoring.get("window_minutes")
    if isinstance(window_minutes, int) and window_minutes > 0:
        return max(DEFAULT_TIMEOUT_SECONDS, window_minutes * 60 + TIMEOUT_BUFFER_SECONDS)

    return DEFAULT_TIMEOUT_SECONDS


async def _check_server_available(host: str, port: int) -> bool:
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session,
            session.get(f"http://{host}:{port}/health") as response,
        ):
            return response.status == 200
    except aiohttp.ClientError:
        return False


async def _wait_for_server(host: str, port: int, timeout_seconds: int = 30) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        while True:
            try:
                async with session.get(f"http://{host}:{port}/health") as response:
                    if response.status == 200:
                        return
            except aiohttp.ClientError:
                pass

            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"Timed out waiting for ESS on {host}:{port}")
            await asyncio.sleep(0.5)


async def _post_trigger_and_wait(
    host: str,
    port: int,
    trigger_path: Path,
    *,
    poll_interval_seconds: float,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    trigger_payload = json.loads(trigger_path.read_text(encoding="utf-8"))
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.post(
            f"http://{host}:{port}/api/v1/deploy",
            json=trigger_payload,
        ) as response:
            body = await response.text()
            if response.status != 202:
                raise RuntimeError(
                    f"Failed to create harness session: HTTP {response.status}: {body[:500]}"
                )
            created = json.loads(body)

        job_id = created["job_id"]
        typer.echo(f"Created harness session {job_id}")

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            async with session.get(f"http://{host}:{port}/api/v1/deploy/{job_id}") as response:
                body = await response.text()
                if response.status != 200:
                    raise RuntimeError(
                        f"Failed to poll session {job_id}: HTTP {response.status}: {body[:500]}"
                    )
                final_status = json.loads(body)

            latest_result = final_status.get("latest_result") or {}
            typer.echo(
                "status="
                f"{final_status.get('status')} "
                "checks="
                f"{final_status.get('checks_completed')}/{final_status.get('checks_planned')} "
                f"severity={latest_result.get('overall_severity', 'NONE')}"
            )

            if final_status.get("status") in {"completed", "failed", "cancelled"}:
                return created, final_status

            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"Timed out waiting for harness session {job_id} to complete")

            await asyncio.sleep(poll_interval_seconds)


def _write_harness_artifacts(
    trace_path: Path,
    created: dict[str, Any],
    final_status: dict[str, Any],
) -> tuple[dict[str, Any], Path, Path, Path, Path]:
    job_id = created["job_id"]
    session_trace_path, digest_path, status_path, summary_path = _artifact_paths(trace_path, job_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(final_status, indent=2), encoding="utf-8")

    summary = _build_summary(final_status)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary, session_trace_path, digest_path, status_path, summary_path


def _print_completion(
    summary: dict[str, Any],
    *,
    status_path: Path,
    summary_path: Path,
    session_trace_path: Path,
    digest_path: Path,
    expected_trace_paths: bool,
) -> None:
    typer.echo("\nHarness completed.")
    typer.echo(json.dumps(summary, indent=2))
    typer.echo(f"Final status: {status_path}")
    if expected_trace_paths:
        typer.echo(f"Expected session trace: {session_trace_path}")
        typer.echo(f"Expected trace digest: {digest_path}")
    else:
        typer.echo(f"Session trace: {session_trace_path}")
        typer.echo(f"Trace digest: {digest_path}")
    typer.echo(f"Summary: {summary_path}")


def _dev_server_command(host: str, port: int) -> str:
    return f"uv run uvicorn src.main:app --host {host} --port {port} --reload"


async def _run_live_command(
    *,
    trigger: Path,
    host: str,
    port: int,
    poll_interval_seconds: float,
    timeout_seconds: int | None,
    trace_path: Path,
) -> int:
    effective_timeout_seconds = _resolve_timeout_seconds(trigger, timeout_seconds)

    if not await _check_server_available(host, port):
        typer.echo(f"ESS is not running at http://{host}:{port}.", err=True)
        typer.echo("Start ESS in development mode with:", err=True)
        typer.echo(_dev_server_command(host, port), err=True)
        return 1

    typer.echo(f"Using running ESS at http://{host}:{port}")
    typer.echo(f"Trigger: {trigger}")

    created, final_status = await _post_trigger_and_wait(
        host,
        port,
        trigger,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=effective_timeout_seconds,
    )
    summary, session_trace_path, digest_path, status_path, summary_path = _write_harness_artifacts(
        trace_path,
        created,
        final_status,
    )
    _print_completion(
        summary,
        status_path=status_path,
        summary_path=summary_path,
        session_trace_path=session_trace_path,
        digest_path=digest_path,
        expected_trace_paths=True,
    )
    return 0 if final_status.get("status") == "completed" else 1


async def _run_degraded_command(
    *,
    trigger: Path,
    host: str,
    port: int,
    poll_interval_seconds: float,
    timeout_seconds: int | None,
    trace_path: Path,
) -> int:
    effective_timeout_seconds = _resolve_timeout_seconds(trigger, timeout_seconds)

    cfg = ESSConfig(
        host=host,
        port=port,
        debug_trace_enabled=True,
        teams_enabled=False,
        agent_trace_path=trace_path,
    )
    app_instance = create_app(config=cfg)
    _install_degraded_datadog_harness(app_instance)

    server = uvicorn.Server(
        uvicorn.Config(
            app_instance,
            host=host,
            port=port,
            log_level="info",
        )
    )
    server_task = asyncio.create_task(server.serve())

    try:
        await _wait_for_server(host, port)
        typer.echo(f"Temporary degraded harness started at http://{host}:{port}")
        typer.echo(f"Trigger: {trigger}")
        typer.echo(
            "Datadog responses are synthetic and intentionally degraded; "
            "Bedrock and Sentry remain live."
        )

        created, final_status = await _post_trigger_and_wait(
            host,
            port,
            trigger,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=effective_timeout_seconds,
        )
        (
            summary,
            session_trace_path,
            digest_path,
            status_path,
            summary_path,
        ) = _write_harness_artifacts(trace_path, created, final_status)
        _print_completion(
            summary,
            status_path=status_path,
            summary_path=summary_path,
            session_trace_path=session_trace_path,
            digest_path=digest_path,
            expected_trace_paths=False,
        )
        return 0 if final_status.get("status") == "completed" else 1
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=15)
        except TimeoutError:
            server.force_exit = True
            await server_task


@app.command("live")
def live_command(
    trigger: Path = typer.Option(
        ...,
        "--trigger",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to the trigger JSON file to post to a running ESS instance.",
    ),
    host: str = typer.Option(DEFAULT_LIVE_HOST, help="ESS host."),
    port: int = typer.Option(DEFAULT_LIVE_PORT, help="ESS port."),
    poll_interval_seconds: float = typer.Option(
        DEFAULT_POLL_INTERVAL_SECONDS,
        help="How often to poll the monitoring session.",
    ),
    timeout_seconds: int | None = typer.Option(
        None,
        help=(
            "Maximum time to wait for the session to reach a terminal state. "
            "Defaults to the trigger window plus a small buffer."
        ),
    ),
    trace_path: Path = typer.Option(
        DEFAULT_LIVE_TRACE_PATH,
        help="Expected ESS trace template path used to derive session trace artifact names.",
    ),
) -> None:
    """Post a trigger to an already running ESS instance and wait for completion."""

    try:
        exit_code = asyncio.run(
            _run_live_command(
                trigger=trigger,
                host=host,
                port=port,
                poll_interval_seconds=poll_interval_seconds,
                timeout_seconds=timeout_seconds,
                trace_path=trace_path,
            )
        )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    raise typer.Exit(code=exit_code)


@app.command("degraded")
def degraded_command(
    trigger: Path = typer.Option(
        ...,
        "--trigger",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to the trigger JSON file to post to the temporary degraded harness.",
    ),
    host: str = typer.Option(DEFAULT_DEGRADED_HOST, help="Temporary ESS host."),
    port: int = typer.Option(DEFAULT_DEGRADED_PORT, help="Temporary ESS port."),
    poll_interval_seconds: float = typer.Option(
        DEFAULT_POLL_INTERVAL_SECONDS,
        help="How often to poll the monitoring session.",
    ),
    timeout_seconds: int | None = typer.Option(
        None,
        help=(
            "Maximum time to wait for the session to reach a terminal state. "
            "Defaults to the trigger window plus a small buffer."
        ),
    ),
    trace_path: Path = typer.Option(
        DEFAULT_DEGRADED_TRACE_PATH,
        help="Trace template path owned by the temporary degraded harness.",
    ),
) -> None:
    """Run a temporary degraded ESS instance and force the Datadog-to-Sentry path."""

    try:
        exit_code = asyncio.run(
            _run_degraded_command(
                trigger=trigger,
                host=host,
                port=port,
                poll_interval_seconds=poll_interval_seconds,
                timeout_seconds=timeout_seconds,
                trace_path=trace_path,
            )
        )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    raise typer.Exit(code=exit_code)


def main() -> None:
    app()
