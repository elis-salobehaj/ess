---
title: "ESS Deliverable 3 — Log Scout: Lightweight Syslog Search Agent"
status: backlog
priority: high
estimated_hours: 30-40
created: 2026-03-22
date_updated: 2026-03-22
parent_plan: plans/active/ess-eye-of-sauron-service.md
related_files:
  - src/server.py
  - src/tools/log_search.py
  - src/config_loader.py
  - config/services.yaml
tags:
  - ess
  - syslog
  - log-scout
  - ripgrep
  - log-search
completion:
  - "# Phase L1 — Log Scout Service (runs on syslog server)"
  - [ ] L1.1 Scaffold Log Scout project (Python, uv, FastAPI)
  - [ ] L1.2 Port ripgrep search from log-ai (subprocess, UTC, services.yaml)
  - [ ] L1.3 Implement HTTP API endpoint (POST /search)
  - [ ] L1.4 Implement service name resolution from services.yaml
  - [ ] L1.5 Implement time-range filtering (minutes_back → UTC dir matching)
  - [ ] L1.6 Implement result truncation and pagination
  - [ ] L1.7 Unit tests with fixture log files and mocked ripgrep
  - [ ] L1.8 Documentation — README, API spec
  - "# Phase L2 — Deployment & Hardening"
  - [ ] L2.1 Systemd service unit for syslog server deployment
  - [ ] L2.2 Health endpoint and structured logging
  - [ ] L2.3 Basic auth or network-level access restriction
  - [ ] L2.4 Rate limiting (max concurrent ripgrep processes)
  - [ ] L2.5 Integration test on syslog server with real logs
  - "# Phase L3 — ESS Client Adapter"
  - [ ] L3.1 Implement LogScoutTool adapter in ESS (HTTP client)
  - [ ] L3.2 Per-service log_search_host routing
  - [ ] L3.3 ToolResult normalisation for log search responses
  - [ ] L3.4 Define Bedrock-compatible tool schema
  - [ ] L3.5 Write system-prompt fragment for log search usage
  - [ ] L3.6 Unit tests for ESS adapter with mocked HTTP
  - [ ] L3.7 End-to-end test — ESS calls real Log Scout
---

# ESS Deliverable 3 — Log Scout: Lightweight Syslog Search Agent

> Extracted from the [ESS master plan](ess-eye-of-sauron-service.md), Decision 3.
> This deliverable creates the ESS Log Scout — a lightweight HTTP microservice
> running on syslog servers — plus the ESS-side client adapter to call it.

## Problem

ESS needs to search application logs on syslog servers to correlate log-level
errors with Datadog/Sentry signals. But ESS should NOT:

- Run on the syslog server directly (limits deployment flexibility)
- Mount log directories via NFS (network traffic for raw log data, latency)
- SSH into syslog servers (complexity, credential management)
- Duplicate log-ai's full MCP server (overkill for search-only use case)

## Solution: Local Log Scout

A lightweight HTTP microservice ("ESS Log Scout") runs on each syslog server.
It performs ripgrep-based log search **locally** and returns only matched entries
to ESS over HTTP. This keeps:

- **Log I/O local** — ripgrep runs on the same machine as the log files
- **Network traffic minimal** — only search results cross the wire
- **ESS stateless** — ESS can run anywhere (K8s, ECS, etc.) without filesystem
  access to logs
- **Multi-server support** — different syslog servers can each run a scout; ESS
  routes to the right one per service

```
┌───────────────────┐     HTTP POST /search      ┌────────────────────┐
│                   │ ─────────────────────────── │                    │
│   ESS             │   {"service": "auth",       │  Log Scout         │
│   (K8s/ECS)       │    "query": "error",        │  (syslog server)   │
│                   │    "minutes_back": 10}       │                    │
│                   │ ◄─────────────────────────── │  ripgrep search    │
│                   │   {"matches": [...],         │  services.yaml     │
│                   │    "count": 42,              │  /syslog/app_logs/ │
│                   │    "truncated": false}       │                    │
└───────────────────┘                              └────────────────────┘
```

---

## Part 1: Log Scout Service

The Log Scout is a **separate standalone service** deployed on each syslog server.
It is NOT part of the ESS codebase — it's its own project, but shares patterns
from log-ai.

### L1.1 — Scaffold project

```
ess-log-scout/
├── pyproject.toml
├── README.md
├── config/
│   ├── .env.example
│   └── services.yaml          # shared format with log-ai
├── src/
│   ├── __init__.py
│   ├── main.py                # FastAPI app
│   ├── config.py              # pydantic-settings config
│   ├── search.py              # ripgrep subprocess wrapper
│   └── service_resolver.py    # services.yaml name resolution
├── tests/
│   ├── test_search.py
│   ├── test_service_resolver.py
│   └── fixtures/
│       └── sample_logs/       # minimal log files for testing
└── ess-log-scout.service      # systemd unit
```

Dependencies (minimal):
```toml
[project]
name = "ess-log-scout"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "pydantic-settings>=2.0",
    "pyyaml>=6.0",        # services.yaml parsing
]
```

System dependency: `ripgrep` (installed via `apt install ripgrep` on the syslog
server).

---

### L1.2 — Port ripgrep search from log-ai

The core search logic is adapted from log-ai's `search_logs` implementation. Key
components to port:

1. **Ripgrep subprocess execution** — `asyncio.create_subprocess_exec("rg", ...)`
   with `--json` output for structured matches
2. **Log directory resolution** — use `services.yaml` to map service names to log
   directory paths (e.g., `hub-ca-auth` → `/syslog/application_logs/hub-ca-auth/`)
3. **UTC time-range filtering** — target only date-stamped directories/files
   within the requested time window (log-ai's proven pattern of resolving UTC
   directories to avoid scanning irrelevant files)

```python
import asyncio
import json
from asyncio.subprocess import PIPE
from datetime import datetime, timezone


class LogSearcher:
    """Execute ripgrep searches on local log files."""

    def __init__(self, config: "ScoutConfig"):
        self.log_base_path = config.log_base_path
        self._semaphore = asyncio.Semaphore(config.max_concurrent_searches)

    async def search(
        self,
        log_path: str,
        query: str,
        minutes_back: int = 10,
        max_results: int = 200,
    ) -> dict:
        """Search log files at the given path for the query.

        Args:
            log_path: Absolute path to the service's log directory
            query: ripgrep search pattern
            minutes_back: How far back to search
            max_results: Maximum number of matches to return

        Returns:
            Dict with matches, count, and truncation flag.
        """
        async with self._semaphore:
            # Determine which date directories/files to scan
            target_paths = self._resolve_time_range(log_path, minutes_back)
            if not target_paths:
                return {"matches": [], "count": 0, "truncated": False}

            proc = await asyncio.create_subprocess_exec(
                "rg",
                "--json",                  # structured JSON output
                "--max-count", str(max_results),
                "--no-heading",
                "-i",                      # case insensitive
                query,
                *target_paths,
                stdout=PIPE, stderr=PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=120,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {"matches": [], "count": 0, "error": "Search timed out"}

            matches = self._parse_rg_json(stdout.decode(errors="replace"))
            truncated = len(matches) >= max_results

            return {
                "matches": matches[:max_results],
                "count": len(matches),
                "truncated": truncated,
            }

    def _resolve_time_range(self, log_path: str, minutes_back: int) -> list[str]:
        """Determine which log files/directories fall within the time range.

        Adapted from log-ai's UTC directory resolution pattern.
        """
        from datetime import timedelta
        from pathlib import Path

        now = datetime.now(timezone.utc)
        paths = []

        # Check for date-stamped directories (YYYY-MM-DD format)
        base = Path(log_path)
        if not base.exists():
            return []

        for i in range(minutes_back // (24 * 60) + 2):  # cover date boundaries
            date = now - timedelta(days=i)
            date_dir = base / date.strftime("%Y-%m-%d")
            if date_dir.exists():
                paths.append(str(date_dir))

        # If no date dirs found, search the base directory itself
        if not paths and base.exists():
            paths.append(str(base))

        return paths

    @staticmethod
    def _parse_rg_json(output: str) -> list[dict]:
        """Parse ripgrep --json output into a list of match dicts."""
        matches = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("type") == "match":
                    data = entry["data"]
                    matches.append({
                        "path": data.get("path", {}).get("text", ""),
                        "line_number": data.get("line_number"),
                        "text": data.get("lines", {}).get("text", "").strip(),
                    })
            except json.JSONDecodeError:
                continue
        return matches
```

---

### L1.3 — HTTP API endpoint

Minimal FastAPI app with a single search endpoint:

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="ESS Log Scout", version="0.1.0")

class SearchRequest(BaseModel):
    service: str = Field(description="Service name (resolved via services.yaml)")
    query: str = Field(description="Search pattern (ripgrep syntax)")
    minutes_back: int = Field(default=10, ge=1, le=1440)
    max_results: int = Field(default=200, ge=1, le=1000)

class SearchResponse(BaseModel):
    service: str
    log_path: str
    matches: list[dict]
    count: int
    truncated: bool
    search_duration_ms: int
    error: str | None = None

@app.post("/search", response_model=SearchResponse)
async def search_logs(req: SearchRequest) -> SearchResponse:
    """Search local log files for the given service and query."""
    import time
    start = time.monotonic()

    # Resolve service name to log directory
    log_path = service_resolver.resolve(req.service)
    if log_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown service: {req.service}"
        )

    result = await log_searcher.search(
        log_path=log_path,
        query=req.query,
        minutes_back=req.minutes_back,
        max_results=req.max_results,
    )

    elapsed = int((time.monotonic() - start) * 1000)
    return SearchResponse(
        service=req.service,
        log_path=log_path,
        matches=result.get("matches", []),
        count=result.get("count", 0),
        truncated=result.get("truncated", False),
        search_duration_ms=elapsed,
        error=result.get("error"),
    )

@app.get("/health")
async def health():
    """Health check for container orchestrators."""
    return {"status": "ok"}
```

---

### L1.4 — Service name resolution

Uses the same `services.yaml` format as log-ai:

```python
import yaml
from pathlib import Path


class ServiceResolver:
    """Resolve service names to log directory paths using services.yaml."""

    def __init__(self, services_yaml_path: str, log_base_path: str):
        self.log_base_path = log_base_path
        self._services: dict[str, dict] = {}
        self._load(services_yaml_path)

    def _load(self, path: str):
        with open(path) as f:
            data = yaml.safe_load(f)
        for svc in data.get("services", []):
            name = svc.get("name", "")
            self._services[name.lower()] = svc

    def resolve(self, service_name: str) -> str | None:
        """Resolve a service name to its log directory path.

        Returns None if the service is not found.
        """
        svc = self._services.get(service_name.lower())
        if svc is None:
            # Try partial match (e.g., "auth" matches "hub-ca-auth")
            for name, data in self._services.items():
                if service_name.lower() in name:
                    svc = data
                    break

        if svc is None:
            return None

        return str(Path(self.log_base_path) / svc["name"])
```

The `services.yaml` file is the same format used by log-ai — the Log Scout can
share the same file or use a subset relevant to the syslog server it runs on.

---

### L1.5 — Time-range filtering

Already implemented in `LogSearcher._resolve_time_range()` above. The key
pattern (from log-ai) is:

1. Logs are stored in date-stamped directories: `{service}/2026-03-22/`
2. Given `minutes_back=10`, calculate which date directories to scan
3. Only pass those directories to ripgrep — avoids scanning weeks of logs

For logs without date directories (flat file structure), fall back to scanning
the entire service directory. Ripgrep's speed makes this viable for reasonable
file sizes.

---

### L1.6 — Result truncation

- Default `max_results=200` — prevents returning megabytes of matches
- `ripgrep --max-count` enforces the limit at the search level
- Response includes `truncated: true` flag so ESS knows to refine the query
- ESS can request more results by increasing `max_results` (up to 1000)

---

### L1.7 — Unit tests

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.fixture
def sample_rg_output():
    """Minimal ripgrep --json output."""
    return '\n'.join([
        '{"type":"match","data":{"path":{"text":"/syslog/app/auth/2026-03-22/app.log"},'
        '"line_number":42,"lines":{"text":"2026-03-22T14:30:05Z ERROR auth failed"}}}',
        '{"type":"match","data":{"path":{"text":"/syslog/app/auth/2026-03-22/app.log"},'
        '"line_number":43,"lines":{"text":"2026-03-22T14:30:06Z ERROR timeout"}}}',
    ])

async def test_search_parses_rg_json(sample_rg_output, searcher):
    with patch("asyncio.create_subprocess_exec") as mock:
        mock.return_value = AsyncMock(
            communicate=AsyncMock(return_value=(sample_rg_output.encode(), b"")),
            returncode=0,
        )
        result = await searcher.search("/syslog/app/auth", "error")
        assert result["count"] == 2
        assert result["matches"][0]["line_number"] == 42

async def test_search_timeout(searcher):
    with patch("asyncio.create_subprocess_exec") as mock:
        mock.return_value = AsyncMock(
            communicate=AsyncMock(side_effect=asyncio.TimeoutError()),
            kill=AsyncMock(),
        )
        result = await searcher.search("/syslog/app/auth", "error")
        assert result["error"] == "Search timed out"

async def test_service_resolver_exact_match(resolver):
    path = resolver.resolve("hub-ca-auth")
    assert path.endswith("hub-ca-auth")

async def test_service_resolver_partial_match(resolver):
    path = resolver.resolve("auth")
    assert path is not None

async def test_service_resolver_unknown(resolver):
    assert resolver.resolve("nonexistent") is None

async def test_api_search_invalid_service(client):
    resp = await client.post("/search", json={"service": "nope", "query": "error"})
    assert resp.status_code == 404

async def test_api_search_success(client, mock_searcher):
    resp = await client.post("/search", json={"service": "hub-ca-auth", "query": "error"})
    assert resp.status_code == 200
    data = resp.json()
    assert "matches" in data
    assert "count" in data
```

---

## Part 2: Deployment & Hardening

### L2.1 — Systemd service

The Log Scout runs as a systemd service on each syslog server:

```ini
# /etc/systemd/system/ess-log-scout.service
[Unit]
Description=ESS Log Scout — Local Log Search Agent
After=network.target

[Service]
Type=exec
User=logscout
Group=logscout
WorkingDirectory=/opt/ess-log-scout
ExecStart=/opt/ess-log-scout/.venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8090
Restart=always
RestartSec=5
Environment=LOG_BASE_PATH=/syslog/application_logs
Environment=SERVICES_YAML_PATH=/opt/ess-log-scout/config/services.yaml

[Install]
WantedBy=multi-user.target
```

Install:
```bash
# On syslog server
sudo apt install ripgrep
cd /opt/ess-log-scout
uv sync --frozen
sudo systemctl enable --now ess-log-scout
```

---

### L2.2 — Health and logging

- `/health` returns `{"status": "ok"}` — for monitoring
- Structured JSON logs via `structlog` or Python logging with JSON formatter
- Log every search request: service, query, result count, duration
- Do NOT log the actual log content matches (security, PII)

---

### L2.3 — Access restriction

For v1, network-level restriction is sufficient:
- Log Scout listens on `0.0.0.0:8090` (or a configurable port)
- Firewall rules restrict access to ESS's IP range only
- No public exposure

Future: Add API key auth if the network perimeter is insufficient:
```python
# Optional API key validation
API_KEY = config.api_key  # from .env
if API_KEY:
    @app.middleware("http")
    async def validate_api_key(request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        key = request.headers.get("X-API-Key")
        if key != API_KEY:
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        return await call_next(request)
```

---

### L2.4 — Rate limiting

`LogSearcher` uses `asyncio.Semaphore(config.max_concurrent_searches)` (default:
5) to prevent too many concurrent ripgrep processes. This protects the syslog
server's I/O bandwidth.

Additional guardrails:
- `max_results` capped at 1000 per request
- `minutes_back` capped at 1440 (24 hours) to prevent full-filesystem scans
- Ripgrep subprocess timeout of 120s

---

### L2.5 — Integration test

Run on the actual syslog server:
```bash
# Start Log Scout
cd /opt/ess-log-scout && uv run uvicorn src.main:app --port 8090 &

# Test search
curl -s -X POST http://localhost:8090/search \
  -H "Content-Type: application/json" \
  -d '{"service": "hub-ca-auth", "query": "error", "minutes_back": 60}' | python -m json.tool

# Test health
curl -s http://localhost:8090/health

# Verify response structure
curl -s -X POST http://localhost:8090/search \
  -H "Content-Type: application/json" \
  -d '{"service": "hub-ca-auth", "query": "error"}' \
  | python -c "import sys,json; d=json.load(sys.stdin); assert 'matches' in d; print('OK')"
```

---

## Part 3: ESS Client Adapter

This part lives in the ESS codebase — it's the client that calls the Log Scout.

### L3.1 — LogScoutTool adapter

```python
import aiohttp
from models import ToolResult


class LogScoutTool:
    """Call the remote ESS Log Scout agent for log search."""

    def __init__(self, config: "ESSConfig"):
        self.default_url = config.default_log_scout_url
        self._consecutive_failures: dict[str, int] = {}  # per-host

    async def search(
        self,
        service: str,
        query: str,
        minutes_back: int = 10,
        max_results: int = 200,
        log_scout_url: str | None = None,
    ) -> ToolResult:
        """Search logs via the remote Log Scout agent.

        Args:
            service: Service name (resolved by the scout's services.yaml)
            query: Search pattern (ripgrep syntax)
            minutes_back: How far back to search
            max_results: Max matches to return
            log_scout_url: Override URL for a specific syslog server

        Returns:
            Normalised ToolResult.
        """
        import time
        start = time.monotonic()

        url = log_scout_url or self.default_url
        host = url  # for circuit tracking

        if self._is_circuit_open(host):
            return ToolResult(
                tool="logs.search",
                success=False,
                data={},
                summary=f"Log Scout at {host} disabled after consecutive failures",
                error="Circuit breaker open",
                duration_ms=0,
                raw={},
            )

        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{url}/search",
                    json={
                        "service": service,
                        "query": query,
                        "minutes_back": minutes_back,
                        "max_results": max_results,
                    },
                    timeout=aiohttp.ClientTimeout(total=120),
                )

                elapsed = int((time.monotonic() - start) * 1000)

                if resp.status != 200:
                    self._record_failure(host)
                    text = await resp.text()
                    return ToolResult(
                        tool="logs.search",
                        success=False,
                        data={},
                        summary=f"Log Scout error: {resp.status}",
                        error=text[:200],
                        duration_ms=elapsed,
                        raw={},
                    )

                data = await resp.json()
                self._reset_failures(host)

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            self._record_failure(host)
            return ToolResult(
                tool="logs.search",
                success=False,
                data={},
                summary=f"Log Scout unreachable: {exc}",
                error=str(exc),
                duration_ms=elapsed,
                raw={},
            )

        count = data.get("count", 0)
        truncated = data.get("truncated", False)
        matches = data.get("matches", [])

        # Build a summary of the top matches
        match_summaries = []
        for m in matches[:5]:
            match_summaries.append(m.get("text", "")[:120])

        summary = f"{count} log match(es) for '{query}' in {service}"
        if truncated:
            summary += f" (truncated at {len(matches)})"
        if match_summaries:
            summary += ":\n" + "\n".join(f"  - {s}" for s in match_summaries)

        return ToolResult(
            tool="logs.search",
            success=True,
            data=data,
            summary=summary,
            error=data.get("error"),
            duration_ms=elapsed,
            raw={},
        )

    def _is_circuit_open(self, host: str) -> bool:
        return self._consecutive_failures.get(host, 0) >= 3

    def _record_failure(self, host: str):
        self._consecutive_failures[host] = self._consecutive_failures.get(host, 0) + 1

    def _reset_failures(self, host: str):
        self._consecutive_failures[host] = 0
```

---

### L3.2 — Per-service log_search_host routing

Each service in a deploy trigger can specify its own `log_search_host`:

```python
# From the deploy trigger payload
{
    "services": [
        {
            "name": "hub-ca-auth",
            "log_search_host": "syslog-ca.example.com"  # → http://syslog-ca.example.com:8090
        },
        {
            "name": "hub-us-auth",
            "log_search_host": "syslog-us.example.com"  # → different syslog server
        }
    ]
}
```

The orchestrator passes the host to `LogScoutTool.search()`:
```python
# In the agent's tool dispatch
result = await log_scout.search(
    service=service_target.name,
    query=query,
    log_scout_url=f"http://{service_target.log_search_host}:8090"
    if service_target.log_search_host
    else None,
)
```

If no `log_search_host` is specified, the adapter falls back to
`config.default_log_scout_url`.

---

### L3.3 — ToolResult normalisation

Already built into `LogScoutTool.search()` above — it returns `ToolResult`
directly, consistent with the Datadog and Sentry adapters.

---

### L3.4 — Bedrock tool schema

```python
LOG_SCOUT_TOOLS = [
    {
        "toolSpec": {
            "name": "search_logs",
            "description": (
                "Search application logs on the syslog server for a service. "
                "Runs ripgrep on the remote syslog server via the ESS Log Scout "
                "agent. Returns matched log lines with file path and line number. "
                "Use this on every triage cycle to detect error patterns in raw "
                "logs that may not appear in Datadog or Sentry."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": (
                                "Service name as it appears in services.yaml "
                                "(e.g., 'hub-ca-auth')"
                            )
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "Search pattern — ripgrep syntax. Use simple "
                                "keywords like 'error', 'exception', 'timeout'. "
                                "Supports regex."
                            )
                        },
                        "minutes_back": {
                            "type": "integer",
                            "description": "How many minutes back to search (default: 10)",
                            "default": 10
                        }
                    },
                    "required": ["service", "query"]
                }
            }
        }
    },
]
```

---

### L3.5 — System prompt fragment

```text
## Log Search Tools (via ESS Log Scout)

You have access to raw application log search through:

- `search_logs` — Search application logs on the syslog server for a service

This tool runs ripgrep on the remote syslog server. The search is fast but only
returns matched lines, not surrounding context, so you may need to refine your
query if initial results are unclear.

**When to use**: On every triage cycle, search for "error" or "exception" in the
service logs. If Datadog or Sentry show issues, search for the specific error
message or exception class in logs to get additional context.

**Service name**: Use the service `name` from the deploy context (e.g.,
"hub-ca-auth"), not the Datadog service name. The Log Scout resolves this to the
correct log directory via its services.yaml.

**Tips**:
- Start with broad queries: "error", "exception", "timeout"
- Narrow down with specific patterns: "NullPointerException", "connection refused"
- Check for the deploy SHA: search for the commit hash to confirm the new code is
  actually running
- If results are truncated, refine the query to be more specific
```

---

## Relationship to log-ai

The ESS Log Scout shares **patterns** with log-ai but is a separate service:

| Concern | log-ai | ESS Log Scout |
|---|---|---|
| **Purpose** | Interactive MCP server for AI agents | Automated search for ESS health checks |
| **Interface** | MCP stdio (JSON-RPC) | HTTP (REST API) |
| **Features** | 8 tools (search, Sentry, Datadog, etc.) | 1 tool (search only) |
| **Deployment** | On syslog server, user-invoked | On syslog server, systemd service |
| **Consumer** | VS Code Copilot, Cursor, etc. | ESS orchestrator |
| **services.yaml** | Shared format | Shared format (can be same file) |
| **ripgrep logic** | `search_logs` tool | Ported/adapted from log-ai |

Both services can coexist on the same syslog server. They serve different
purposes and different consumers.

---

## Dependencies

### Log Scout service
| Dependency | Purpose | Version |
|---|---|---|
| Python | Runtime | 3.10+ |
| FastAPI | HTTP API | Latest |
| uvicorn | ASGI server | Latest |
| pydantic-settings | Config | v2 |
| PyYAML | services.yaml parsing | 6.0+ |
| ripgrep | Log search | System package (apt) |

### ESS client adapter
| Dependency | Purpose | Version |
|---|---|---|
| aiohttp | HTTP client | 3.9+ (shared with Teams/Sentry) |

---

## Success Criteria

### Log Scout service
- [ ] Searches local log files via ripgrep subprocess
- [ ] Resolves service names from services.yaml
- [ ] Filters log directories by time range (UTC date matching)
- [ ] Returns structured JSON responses with match count and truncation flag
- [ ] Runs as a systemd service on the syslog server
- [ ] Health endpoint responds correctly
- [ ] Concurrent searches limited by semaphore
- [ ] Handles ripgrep timeouts gracefully

### ESS client adapter
- [ ] Calls Log Scout HTTP endpoint asynchronously
- [ ] Routes to per-service syslog servers via `log_search_host`
- [ ] Falls back to `default_log_scout_url` when no host specified
- [ ] Circuit breaker per host after 3 consecutive failures
- [ ] Returns normalised ToolResult compatible with the orchestrator
- [ ] Tool schema works with Bedrock converse toolConfig
- [ ] System prompt guides the agent on service name usage and query refinement
