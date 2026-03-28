"""ESS Datadog tool adapter — Pup CLI async subprocess executor.

All Datadog queries are routed through this module.  ``PupTool`` wraps
``asyncio.create_subprocess_exec`` with:

- A global ``asyncio.Semaphore`` to cap concurrent subprocess calls (default: 10).
- A simple circuit breaker that opens after 3 consecutive failures and blocks
  further calls until the ESS process is restarted.
- Per-call timeouts (default: ``config.pup_default_timeout`` seconds).
- ``FORCE_AGENT_MODE=1`` env var for machine-optimised JSON output.

One ``PupTool`` instance is shared across all monitoring sessions in a single
ESS process — the semaphore and circuit breaker are therefore process-wide, not
per-session.  This ensures a burst of concurrent deploys cannot spawn an
unbounded number of Pup subprocesses.

Usage::

    tool = PupTool(config=settings)
    result = await tool.get_monitor_status("example-auth-service", "production")
    if result.exit_code == 0:
        print(result.data)
"""

from __future__ import annotations

import asyncio
import json
import time
from asyncio.subprocess import PIPE
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from src.config import ESSConfig

logger: structlog.BoundLogger = structlog.get_logger(__name__)  # type: ignore[assignment]

_CIRCUIT_BREAKER_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Raw result type
# ---------------------------------------------------------------------------


@dataclass
class PupResult:
    """Raw result from a single Pup CLI invocation.

    Attributes:
        command:     Full CLI command string (for logging / debugging).
        exit_code:   Process exit code, or ``-1`` for timeout / circuit-open.
        data:        Parsed JSON payload — a ``dict``, a ``list``, or ``None``
                     on failure.
        stderr:      Decoded stderr output (may be empty on success).
        duration_ms: Wall-clock duration of the Pup call in milliseconds.
    """

    command: str
    exit_code: int
    data: dict[str, Any] | list[Any] | None
    stderr: str
    duration_ms: int


# ---------------------------------------------------------------------------
# PupTool
# ---------------------------------------------------------------------------


class PupTool:
    """Execute Datadog Pup CLI commands as async subprocesses.

    Lifecycle::

        tool = PupTool(config=settings)
        # Use triage methods each health-check cycle:
        result = await tool.get_monitor_status(service, env)
        result = await tool.search_error_logs(service)
        result = await tool.get_apm_stats(service, env)
        # Use investigation methods when anomalies are detected:
        result = await tool.get_apm_operations(service, env)
        ...
    """

    def __init__(self, config: ESSConfig) -> None:
        self.config = config
        self._semaphore = asyncio.Semaphore(config.pup_max_concurrent)
        self._consecutive_failures = 0
        self._circuit_open = False

    # ------------------------------------------------------------------
    # Core executor
    # ------------------------------------------------------------------

    async def execute(self, args: list[str], timeout: int | None = None) -> PupResult:
        """Run ``pup <args> --output json`` and return a parsed result.

        Args:
            args:    CLI arguments passed after ``pup``, e.g.
                     ``["monitors", "list", "--tags=service:svc,env:prod"]``.
            timeout: Per-call timeout in seconds.  Defaults to
                     ``config.pup_default_timeout``.

        Returns:
            On success: ``PupResult`` with ``exit_code=0`` and parsed JSON in
            ``data``.  On any failure: ``PupResult`` with non-zero
            ``exit_code``, ``data=None``, and an error message in ``stderr``.
        """
        effective_timeout = timeout if timeout is not None else self.config.pup_default_timeout
        command_str = "pup " + " ".join(args)

        if self._circuit_open:
            logger.warning("pup_circuit_open", command=command_str)
            return PupResult(
                command=command_str,
                exit_code=-1,
                data=None,
                stderr="Circuit breaker open — Pup CLI disabled after consecutive failures",
                duration_ms=0,
            )

        async with self._semaphore:
            env = self.config.pup_subprocess_environment()

            t_start = time.monotonic()

            try:
                proc = await asyncio.create_subprocess_exec(
                    "pup",
                    *args,
                    "--output",
                    "json",
                    stdout=PIPE,
                    stderr=PIPE,
                    env=env,
                )
            except FileNotFoundError:
                elapsed = int((time.monotonic() - t_start) * 1000)
                self._record_failure()
                logger.error("pup_not_found", command=command_str)
                return PupResult(
                    command=command_str,
                    exit_code=-1,
                    data=None,
                    stderr="pup executable not found — is it installed and on $PATH?",
                    duration_ms=elapsed,
                )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=float(effective_timeout),
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                elapsed = int((time.monotonic() - t_start) * 1000)
                self._record_failure()
                logger.warning(
                    "pup_timeout",
                    command=command_str,
                    timeout_s=effective_timeout,
                    duration_ms=elapsed,
                )
                return PupResult(
                    command=command_str,
                    exit_code=-1,
                    data=None,
                    stderr=f"Timed out after {effective_timeout}s",
                    duration_ms=elapsed,
                )

            elapsed = int((time.monotonic() - t_start) * 1000)
            exit_code = proc.returncode or 0
            stderr_str = stderr.decode(errors="replace")

            if exit_code != 0:
                self._record_failure()
                logger.warning(
                    "pup_non_zero_exit",
                    command=command_str,
                    exit_code=exit_code,
                    duration_ms=elapsed,
                    stderr=stderr_str[:200],
                )
                return PupResult(
                    command=command_str,
                    exit_code=exit_code,
                    data=None,
                    stderr=stderr_str,
                    duration_ms=elapsed,
                )

            # Success — reset the circuit breaker consecutive-failure counter.
            self._consecutive_failures = 0

            try:
                data: dict[str, Any] | list[Any] = json.loads(stdout)
            except json.JSONDecodeError:
                # Pup returned non-JSON output (unexpected in agent mode).
                # Wrap it so callers always receive a dict.
                data = {"raw_output": stdout.decode(errors="replace")}

            logger.debug("pup_ok", command=command_str, duration_ms=elapsed)
            return PupResult(
                command=command_str,
                exit_code=0,
                data=data,
                stderr=stderr_str,
                duration_ms=elapsed,
            )

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open = True
            logger.error(
                "pup_circuit_opened",
                consecutive_failures=self._consecutive_failures,
            )

    # ------------------------------------------------------------------
    # Triage methods — run on every health-check cycle
    # ------------------------------------------------------------------

    async def get_monitor_status(self, service: str, env: str) -> PupResult:
        """List Datadog monitors tagged with ``service:<service>,env:<env>``."""
        return await self.execute(
            [
                "monitors",
                "list",
                f"--tags=service:{service},env:{env}",
            ]
        )

    async def search_error_logs(self, service: str, minutes: int = 10) -> PupResult:
        """Search Datadog logs for error-level entries in the last ``minutes``."""
        return await self.execute(
            [
                "logs",
                "search",
                f"--query=service:{service} status:error",
                f"--from={minutes}m",
            ]
        )

    async def get_apm_stats(self, service: str, env: str) -> PupResult:
        """Get APM latency, error rate, and throughput stats for ``env``.

        Returns stats for *all* services in the environment — ``pup apm
        services stats`` does not support per-service filtering.  The caller
        should filter by ``service`` name in the result payload.
        ``service`` is retained as a parameter for call-site context and
        forward-compatibility.
        """
        return await self.execute(
            [
                "apm",
                "services",
                "stats",
                f"--env={env}",
            ]
        )

    # ------------------------------------------------------------------
    # Investigation methods — run when triage detects anomalies
    # ------------------------------------------------------------------

    async def get_recent_incidents(self) -> PupResult:
        """List active Datadog incidents."""
        return await self.execute(["incidents", "list"])

    async def get_infrastructure_health(self, service: str) -> PupResult:
        """List host health (CPU, memory, disk) for hosts running ``service``."""
        return await self.execute(
            [
                "infrastructure",
                "hosts",
                "list",
                f"--filter=service:{service}",
            ]
        )

    async def get_apm_operations(self, service: str, env: str) -> PupResult:
        """Get per-operation APM breakdown — identifies slow / high-error endpoints."""
        return await self.execute(
            [
                "apm",
                "services",
                "operations",
                f"--service={service}",
                f"--env={env}",
            ]
        )

    async def search_warning_logs(self, service: str, minutes: int = 10) -> PupResult:
        """Search Datadog logs for warning-level entries in the last ``minutes``."""
        return await self.execute(
            [
                "logs",
                "search",
                f"--query=service:{service} status:warn",
                f"--from={minutes}m",
            ]
        )

    async def get_apm_resources(self, service: str, operation: str, env: str) -> PupResult:
        """Get resource-level stats for a specific operation within ``service``."""
        return await self.execute(
            [
                "apm",
                "services",
                "resources",
                f"--service={service}",
                f"--operation={operation}",
                f"--env={env}",
            ]
        )
