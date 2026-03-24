---
title: "ESS Deliverable 1 — Datadog Pup CLI Integration"
status: active
priority: high
estimated_hours: 25-35
created: 2026-03-22
date_updated: 2026-03-24
parent_plan: plans/active/ess-eye-of-sauron-service.md
related_files:
    - src/main.py
  - src/tools/pup_tool.py
  - src/tools/normalise.py
  - src/config.py
  - src/models.py
    - tests/test_trigger.py
    - tests/test_main.py
  - tests/test_pup_tool.py
  - Dockerfile
  - .dockerignore
    - docs/guides/TRIGGER_END_TO_END_DATADOG_PUP_INTEGRATION.md
tags:
  - ess
  - datadog
  - pup-cli
  - observability
completion:
  - "# Phase D1 — Pup CLI Adapter"
  - [x] D1.1 Install and validate Pup CLI in dev environment
  - [x] D1.2 Implement PupTool async subprocess executor
  - [x] D1.3 Implement health-check convenience methods
  - [x] D1.4 Implement investigation convenience methods
  - [x] D1.5 Error handling, timeouts, and structured output parsing
  - [x] D1.6 Unit tests with mocked subprocess responses
  - "# Phase D2 — Docker & Auth"
  - [x] D2.1 Dockerfile — install Pup binary
  - [x] D2.2 Auth flow — DD_API_KEY + DD_APP_KEY via ESSConfig
  - [x] D2.3 Rate limiting (max concurrent subprocess calls)
  - [x] D2.4 Circuit breaker for consecutive failures
  - [x] D2.5 Integration test with real Datadog (marked @pytest.mark.integration)
  - [x] D2.6 Expose latest health-check findings on session status endpoint
  - "# Phase D3 — Agent Tool Definitions"
  - [ ] D3.1 Define Bedrock-compatible tool schemas for Pup commands
  - [ ] D3.2 Map tool results to ToolResult normalised format
  - [ ] D3.3 Write system-prompt fragments for Datadog tool usage
  - [ ] D3.4 End-to-end test — mock LLM calls Pup tools
  - [ ] D3.5 Documentation — Pup tool integration guide
---

# ESS Deliverable 1 — Datadog Pup CLI Integration

> Extracted from the [ESS master plan](ess-eye-of-sauron-service.md). This
> deliverable covers everything needed for ESS to query Datadog via the Pup CLI.

## Scope

This plan delivers a production-ready Datadog tool adapter for ESS using the
[Datadog Pup CLI](https://github.com/datadog-labs/pup) (v0.33+, Apache-2.0).
Once complete, the ESS AI orchestrator can call any of the following Datadog
capabilities as tool calls:

- **Monitors**: list active monitors, filter by service/env tags, detect alerting
- **Logs**: search error/warning logs within a time window
- **APM**: service stats (latency, error rate, throughput), operations breakdown
- **Incidents**: list active incidents related to the deployed service
- **Infrastructure**: host health (CPU, memory, disk) for service hosts

## Why Pup CLI

See [Decision 1 in the master plan](ess-eye-of-sauron-service.md) for the full
evaluation. Summary: Pup provides 320+ tested commands across 49 domains with
structured agent-mode JSON output, replacing the manual `datadog-api-client`
approach that caused production 400/403 errors in log-ai.

---

## Detailed Design

### D1.1 — Install and validate Pup CLI

**Dev machine (Linux/WSL)**:
```bash
# Option A: Homebrew
brew install datadog-labs/pack/pup

# Option B: Direct binary
curl -fsSL https://github.com/datadog-labs/pup/releases/latest/download/pup-linux-amd64 \
  -o ~/.local/bin/pup && chmod +x ~/.local/bin/pup
```

**Validation**:
```bash
pup --version     # Expect v0.33+
pup monitors list --help  # Confirm command availability

# Test agent mode output
FORCE_AGENT_MODE=1 DD_API_KEY=xxx DD_APP_KEY=xxx \
  pup monitors list --tags="env:production" --output json
```

Confirm:
- Agent mode (`FORCE_AGENT_MODE=1`) returns structured JSON
- `--output json` flag works for non-agent-mode output
- Auth via environment variables works

---

### D1.2 — PupTool async subprocess executor

Core executor that all convenience methods delegate to:

```python
import asyncio
import json
import os
from asyncio.subprocess import PIPE
from dataclasses import dataclass

@dataclass
class PupResult:
    """Raw result from a Pup CLI invocation."""
    command: str
    exit_code: int
    data: dict | list | None
    stderr: str
    duration_ms: int

class PupTool:
    """Execute Datadog Pup CLI commands as async subprocesses."""

    def __init__(self, config: "ESSConfig"):
        self.config = config
        self._semaphore = asyncio.Semaphore(config.pup_max_concurrent)
        self._consecutive_failures = 0
        self._circuit_open = False

    async def execute(self, args: list[str], timeout: int = 60) -> PupResult:
        """Run `pup <args>` and return parsed result.

        Args:
            args: CLI arguments (e.g., ["monitors", "list", "--tags=..."])
            timeout: Subprocess timeout in seconds.

        Returns:
            PupResult with parsed JSON data or None on failure.
        """
        if self._circuit_open:
            return PupResult(
                command=f"pup {' '.join(args)}",
                exit_code=-1,
                data=None,
                stderr="Circuit breaker open — Pup CLI disabled after consecutive failures",
                duration_ms=0,
            )

        async with self._semaphore:
            env = {
                **os.environ,
                "DD_API_KEY": self.config.dd_api_key,
                "DD_APP_KEY": self.config.dd_app_key,
                "DD_SITE": self.config.dd_site,
                "FORCE_AGENT_MODE": "1",
            }

            import time
            start = time.monotonic()

            proc = await asyncio.create_subprocess_exec(
                "pup", *args, "--output", "json",
                stdout=PIPE, stderr=PIPE, env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                self._record_failure()
                return PupResult(
                    command=f"pup {' '.join(args)}",
                    exit_code=-1, data=None,
                    stderr=f"Timed out after {timeout}s",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            elapsed = int((time.monotonic() - start) * 1000)
            exit_code = proc.returncode or 0

            if exit_code != 0:
                self._record_failure()
                return PupResult(
                    command=f"pup {' '.join(args)}",
                    exit_code=exit_code, data=None,
                    stderr=stderr.decode(errors="replace"),
                    duration_ms=elapsed,
                )

            self._consecutive_failures = 0
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                data = {"raw_output": stdout.decode(errors="replace")}

            return PupResult(
                command=f"pup {' '.join(args)}",
                exit_code=0, data=data,
                stderr=stderr.decode(errors="replace"),
                duration_ms=elapsed,
            )

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= 3:
            self._circuit_open = True
```

**Key design decisions**:
- `asyncio.Semaphore` limits concurrent subprocess calls (default: 10)
- Circuit breaker opens after 3 consecutive failures — prevents flooding a broken
  Pup binary with requests during a monitoring session
- `FORCE_AGENT_MODE=1` ensures machine-optimised JSON responses
- Timeout per call defaults to 60s (configurable per method)

---

### D1.3 — Health-check convenience methods (triage)

These are the standard checks ESS runs every cycle:

```python
    # --- Triage methods (run every cycle) ---

    async def get_monitor_status(self, service: str, env: str) -> PupResult:
        """List monitors tagged with service and env."""
        return await self.execute([
            "monitors", "list",
            f"--tags=service:{service},env:{env}",
        ])

    async def search_error_logs(self, service: str, minutes: int = 10) -> PupResult:
        """Search Datadog logs for error-level entries."""
        return await self.execute([
            "logs", "search",
            f"--query=service:{service} status:error",
            f"--from={minutes}m",
        ])

    async def get_apm_stats(self, service: str, env: str) -> PupResult:
        """Get APM latency, error rate, and throughput stats."""
        # NOTE (v0.34.1): pup apm services stats has no --service flag.
        # It returns stats for ALL services in the env; caller filters by service.
        return await self.execute([
            "apm", "services", "stats",
            f"--env={env}",
        ])
```

---

### D1.4 — Investigation convenience methods (deeper analysis)

These run only when triage detects anomalies:

```python
    # --- Investigation methods (run on anomaly) ---

    async def get_recent_incidents(self) -> PupResult:
        """List active Datadog incidents."""
        return await self.execute(["incidents", "list"])

    async def get_infrastructure_health(self, service: str) -> PupResult:
        """List host health for hosts running this service."""
        return await self.execute([
            "infrastructure", "hosts", "list",
            f"--filter=service:{service}",
        ])

    async def get_apm_operations(self, service: str, env: str) -> PupResult:
        """Get per-operation breakdown (slow endpoints, high error routes)."""
        return await self.execute([
            "apm", "services", "operations",
            f"--service={service}",
            f"--env={env}",
        ])

    async def search_warning_logs(self, service: str, minutes: int = 10) -> PupResult:
        """Search Datadog logs for warning-level entries."""
        return await self.execute([
            "logs", "search",
            f"--query=service:{service} status:warn",
            f"--from={minutes}m",
        ])

    async def get_apm_resources(self, service: str, operation: str,
                                 env: str) -> PupResult:
        """Get resource-level stats for a specific operation."""
        return await self.execute([
            "apm", "services", "resources",
            f"--service={service}",
            f"--operation={operation}",
            f"--env={env}",
        ])
```

---

### D1.5 — Error handling and output parsing

Pup agent-mode JSON has a consistent structure. The normalisation layer converts
`PupResult` into the shared `ToolResult` format:

```python
from models import ToolResult

def pup_to_tool_result(pup_result: PupResult, tool_name: str) -> ToolResult:
    """Convert PupResult to the normalised ToolResult format."""
    if pup_result.exit_code != 0 or pup_result.data is None:
        return ToolResult(
            tool=f"datadog.{tool_name}",
            success=False,
            data={},
            summary=f"Pup CLI failed: {pup_result.stderr[:200]}",
            error=pup_result.stderr,
            duration_ms=pup_result.duration_ms,
            raw={"command": pup_result.command, "stderr": pup_result.stderr},
        )

    # Extract summary from agent-mode metadata if present
    summary = ""
    if isinstance(pup_result.data, dict):
        summary = pup_result.data.get("summary", "")
        if not summary and "metadata" in pup_result.data:
            summary = pup_result.data["metadata"].get("description", "")

    return ToolResult(
        tool=f"datadog.{tool_name}",
        success=True,
        data=pup_result.data,
        summary=summary or f"Pup {tool_name} returned successfully",
        error=None,
        duration_ms=pup_result.duration_ms,
        raw={"command": pup_result.command},
    )
```

---

### D2.1 — Dockerfile

Add Pup binary to the ESS container:

```dockerfile
# In ESS Dockerfile
RUN curl -fsSL https://github.com/datadog-labs/pup/releases/latest/download/pup-linux-amd64 \
    -o /usr/local/bin/pup && chmod +x /usr/local/bin/pup
```

Validate in CI:
```bash
docker run ess:test pup --version
```

---

### D2.2 — Auth flow

ESSConfig fields for Datadog:

```python
# In config.py (ESSConfig)
dd_api_key: str           # DD_API_KEY env var
dd_app_key: str           # DD_APP_KEY env var
dd_site: str = "datadoghq.com"  # DD_SITE
pup_max_concurrent: int = 10    # max parallel Pup calls
pup_default_timeout: int = 60   # seconds per call
```

The `PupTool.__init__` reads these from config and passes them as environment
variables to each subprocess. No OAuth setup for v1 — API key auth is sufficient
since Pup handles api/app key flow natively.

**Future**: If Pup OAuth2+PKCE is needed for stricter scoping, Pup supports
`pup login` which caches tokens. This can be run at container startup.

---

### D2.3 — Rate limiting

Already built into `PupTool.execute()` via `asyncio.Semaphore(config.pup_max_concurrent)`.

Additional safeguard: if ESS has N active monitoring sessions, each with M
services, the total concurrent Pup calls is bounded by the semaphore globally,
not per-session. This prevents a burst of deploys from spawning hundreds of
subprocess calls.

---

### D2.4 — Circuit breaker

Built into `PupTool.execute()`. After 3 consecutive failures (any method), the
circuit opens and all subsequent calls return immediately with an error `ToolResult`.
The circuit state is per `PupTool` instance (one per ESS process).

The agent should be informed when the circuit is open so it can note "Datadog
tools unavailable" in the health report rather than silently skipping checks.

**Future**: Add a half-open state that retries one call after a configurable
cooldown period (e.g., 60s).

---

### D2.5 — Integration tests

```python
@pytest.mark.integration
async def test_pup_monitors_list_real():
    """Call Pup monitors list against real Datadog — requires DD_API_KEY."""
    tool = PupTool(config=ESSConfig())
    result = await tool.get_monitor_status("example-auth-service", "production")
    assert result.exit_code == 0
    assert result.data is not None

@pytest.mark.integration
async def test_pup_apm_stats_real():
    """Call Pup APM stats against real Datadog."""
    tool = PupTool(config=ESSConfig())
    result = await tool.get_apm_stats("example-auth-service", "production")
    assert result.exit_code == 0

@pytest.mark.integration
async def test_pup_logs_search_real():
    """Call Pup logs search against real Datadog."""
    tool = PupTool(config=ESSConfig())
    result = await tool.search_error_logs("example-auth-service", minutes=30)
    assert result.exit_code == 0
```

Run with: `uv run pytest tests/test_pup_tool.py -m integration`

---

### D2.6 — Expose latest health-check findings on session status endpoint

For quick first-iteration inspection during development, `GET /api/v1/deploy/{job_id}`
returns the latest completed `HealthCheckResult` as `latest_result`.

This keeps the implementation simple:

- no new persistence layer
- no separate results endpoint
- no pagination or history querying yet

Example response shape:

```json
{
    "job_id": "ess-ffd07a29",
    "status": "running",
    "checks_completed": 1,
    "checks_planned": 2,
    "latest_result": {
        "cycle_number": 1,
        "overall_severity": "HEALTHY",
        "findings": [
            {
                "tool": "datadog.apm_stats",
                "severity": "HEALTHY",
                "summary": "example-well-service: Pup apm_stats returned successfully"
            }
        ],
        "services_checked": ["example-well-service"],
        "raw_tool_outputs": {
            "example-well-service:datadog.apm_stats": {
                "success": true,
                "summary": "Pup apm_stats returned successfully"
            }
        }
    }
}
```

This is intended for local debugging and early end-to-end validation. A richer
history/results API can be added later if Phase 3 or later workflows need it.

---

### D3.1 — Bedrock tool schemas

Tool definitions for the LLM (Bedrock converse `toolConfig` format):

```python
DATADOG_TOOLS = [
    {
        "toolSpec": {
            "name": "datadog_monitor_status",
            "description": (
                "Check Datadog monitor status for a service. Returns all monitors "
                "tagged with the service name and environment, including their "
                "current state (OK, Alert, Warn, No Data)."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": "Datadog service name (e.g., 'example-auth-service')"
                        },
                        "environment": {
                            "type": "string",
                            "description": "Environment tag (e.g., 'production')"
                        }
                    },
                    "required": ["service", "environment"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "datadog_error_logs",
            "description": (
                "Search Datadog logs for error-level entries for a service within "
                "a recent time window. Use this to detect new errors after a deploy."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string", "description": "Datadog service name"},
                        "minutes_back": {
                            "type": "integer",
                            "description": "How many minutes back to search (default: 10)",
                            "default": 10
                        }
                    },
                    "required": ["service"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "datadog_apm_stats",
            "description": (
                "Get APM performance statistics for a service: latency (p50/p95/p99), "
                "error rate, and request throughput. Use this to detect latency "
                "regressions or elevated error rates after a deploy."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string", "description": "Datadog service name"},
                        "environment": {"type": "string", "description": "Environment tag"}
                    },
                    "required": ["service", "environment"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "datadog_incidents",
            "description": (
                "List active Datadog incidents. Use this during investigation to "
                "check if there is already an open incident related to the deploy."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {},
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "datadog_infrastructure_health",
            "description": (
                "Check host-level health (CPU, memory, disk) for hosts running "
                "a specific service. Use during investigation to rule out "
                "infrastructure issues."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string", "description": "Datadog service name"}
                    },
                    "required": ["service"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "datadog_apm_operations",
            "description": (
                "Get per-operation APM breakdown for a service: identifies slow "
                "endpoints or high-error routes. Use during investigation to "
                "narrow down which endpoint is causing issues."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string", "description": "Datadog service name"},
                        "environment": {"type": "string", "description": "Environment tag"}
                    },
                    "required": ["service", "environment"]
                }
            }
        }
    },
]
```

---

### D3.2 — Tool dispatch mapping

The orchestrator maps tool call names to `PupTool` methods:

```python
DATADOG_DISPATCH = {
    "datadog_monitor_status": lambda tool, args: tool.get_monitor_status(
        args["service"], args["environment"]
    ),
    "datadog_error_logs": lambda tool, args: tool.search_error_logs(
        args["service"], args.get("minutes_back", 10)
    ),
    "datadog_apm_stats": lambda tool, args: tool.get_apm_stats(
        args["service"], args["environment"]
    ),
    "datadog_incidents": lambda tool, args: tool.get_recent_incidents(),
    "datadog_infrastructure_health": lambda tool, args: tool.get_infrastructure_health(
        args["service"]
    ),
    "datadog_apm_operations": lambda tool, args: tool.get_apm_operations(
        args["service"], args["environment"]
    ),
}
```

Each dispatch returns a `PupResult` which is then normalised to `ToolResult` via
`pup_to_tool_result()`.

---

### D3.3 — System prompt fragment

```text
## Datadog Tools (via Pup CLI)

You have access to Datadog observability data through the following tools:

**Triage (run on every cycle):**
- `datadog_monitor_status` — Check if any monitors are alerting for this service
- `datadog_error_logs` — Search for error-level log entries since deploy
- `datadog_apm_stats` — Get latency/error-rate/throughput metrics

**Investigation (run when anomalies detected):**
- `datadog_incidents` — Check for open incidents
- `datadog_infrastructure_health` — Check host CPU/memory/disk
- `datadog_apm_operations` — Identify which specific endpoint is problematic

When checking Datadog, always use the `datadog_service_name` from the deploy
context, not the log service name. These may differ (e.g., log name
"hub-ca-auth" → Datadog name "example-auth-service").
```

---

## Dependencies

| Dependency | Purpose | Notes |
|---|---|---|
| Datadog Pup CLI | Datadog API access | Binary, v0.33+, installed in Docker image |
| `DD_API_KEY` | Pup auth | Environment variable via ESSConfig |
| `DD_APP_KEY` | Pup auth | Environment variable via ESSConfig |

No Python library dependencies beyond the ESS core (asyncio, json, os).

---

## Success Criteria

- [ ] `PupTool.execute()` runs Pup CLI as async subprocess with JSON output
- [ ] All 6 convenience methods (3 triage + 3 investigation) work correctly
- [ ] Circuit breaker opens after 3 consecutive failures
- [ ] Semaphore limits concurrent Pup calls
- [ ] Integration tests pass against real Datadog (when DD keys provided)
- [ ] Tool schemas are compatible with Bedrock converse `toolConfig` format
- [ ] ToolResult normalisation produces consistent output for the orchestrator
- [ ] Pup binary installs and runs in the ESS Docker container
