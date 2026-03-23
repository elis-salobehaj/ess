---
title: "ESS Deliverable 2 — Sentry Integration"
status: backlog
priority: high
estimated_hours: 20-30
created: 2026-03-22
date_updated: 2026-03-22
parent_plan: plans/active/ess-eye-of-sauron-service.md
related_files:
  - src/tools/sentry_tool.py
  - src/config.py
  - src/models.py
  - tests/test_sentry_tool.py
tags:
  - ess
  - sentry
  - observability
  - self-hosted
completion:
  - "# Phase S1 — Sentry REST API Adapter"
  - [ ] S1.1 Port core Sentry client from log-ai (aiohttp-based)
  - [ ] S1.2 Implement query_issues method (unresolved issues since deploy)
  - [ ] S1.3 Implement get_issue_details method (stack trace, events, users)
  - [ ] S1.4 Implement search_traces method (performance traces)
  - [ ] S1.5 ToolResult normalisation for Sentry responses
  - [ ] S1.6 Unit tests with mocked HTTP responses
  - "# Phase S2 — Auth, Rate Limits & Resilience"
  - [ ] S2.1 Auth — SENTRY_AUTH_TOKEN via ESSConfig, self-hosted host config
  - [ ] S2.2 Rate limiting — respect Sentry API rate limits (429 backoff)
  - [ ] S2.3 Circuit breaker for consecutive failures
  - [ ] S2.4 Integration test with real Sentry (marked @pytest.mark.integration)
  - "# Phase S3 — Agent Tool Definitions"
  - [ ] S3.1 Define Bedrock-compatible tool schemas for Sentry queries
  - [ ] S3.2 Map tool results to ToolResult normalised format
  - [ ] S3.3 Write system-prompt fragments for Sentry tool usage
  - [ ] S3.4 End-to-end test — mock LLM calls Sentry tools
  - [ ] S3.5 Documentation — Sentry tool integration guide
  - "# Phase S4 — Future: Sentry MCP Server Upgrade"
  - [ ] S4.1 Evaluate Sentry MCP stdio transport for self-hosted
  - [ ] S4.2 Implement MCP stdio adapter as alternative backend
  - [ ] S4.3 Feature-flag to switch between REST and MCP backends
---

# ESS Deliverable 2 — Sentry Integration

> Extracted from the [ESS master plan](ess-eye-of-sauron-service.md). This
> deliverable covers everything needed for ESS to query Sentry for post-deploy
> issue detection.

## Scope

This plan delivers a production-ready Sentry tool adapter for ESS. The primary
approach is **direct REST API calls** ported from log-ai's proven
`sentry_integration.py`, with a future upgrade path to the Sentry MCP server.

Once complete, the ESS AI orchestrator can call these Sentry capabilities:

- **Query issues**: list unresolved issues for a project, filtered by time window
- **Issue details**: full stack trace, events timeline, affected users count
- **Search traces**: performance traces to correlate latency with errors

## Strategy: REST First, MCP Later

See [Decision 2 in the master plan](ess-eye-of-sauron-service.md) for the full
evaluation. Summary:

**Phase S1–S3 (this deliverable)**: Direct Sentry REST API calls via aiohttp.
This approach is simpler (no Node.js dependency), proven working in log-ai, and
sufficient for the 3 core queries ESS needs (issues, issue details, traces).

**Phase S4 (future)**: Migrate to `@sentry/mcp-server` stdio transport for
AI-powered search (`search_events`, `search_issues`) and broader Sentry coverage.
This requires Node.js in the container and managing a stdio subprocess, so it's
deferred until ESS v1 is stable.

---

## Detailed Design

### S1.1 — Port core Sentry client from log-ai

Extract the HTTP client pattern from log-ai's Sentry integration. ESS uses
aiohttp (already a dependency for Teams webhook) instead of log-ai's specific
HTTP approach:

```python
import aiohttp
from urllib.parse import quote


class SentryClient:
    """Async Sentry REST API client for self-hosted instances."""

    def __init__(self, config: "ESSConfig"):
        self.host = config.sentry_host
        self.org = config.sentry_org
        self.token = config.sentry_auth_token
        self.base_url = f"https://{self.host}/api/0"
        self._session: aiohttp.ClientSession | None = None
        self._consecutive_failures = 0
        self._circuit_open = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def _request(self, method: str, path: str,
                       params: dict | None = None) -> dict:
        """Make an authenticated request to the Sentry API.

        Handles rate limiting (429) with exponential backoff and circuit
        breaker logic.
        """
        if self._circuit_open:
            raise SentryCircuitOpenError(
                "Sentry API disabled after consecutive failures"
            )

        session = await self._get_session()
        url = f"{self.base_url}{path}"

        import asyncio
        for attempt in range(3):  # retry on 429
            async with session.request(method, url, params=params) as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", 2))
                    await asyncio.sleep(retry_after)
                    continue

                if resp.status >= 400:
                    self._record_failure()
                    text = await resp.text()
                    raise SentryAPIError(
                        f"Sentry API {resp.status}: {text[:200]}"
                    )

                self._consecutive_failures = 0
                return await resp.json()

        self._record_failure()
        raise SentryAPIError("Sentry API rate-limited after 3 retries")

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= 3:
            self._circuit_open = True

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
```

**Key differences from log-ai**:
- Uses aiohttp instead of log-ai's HTTP approach — consistent with ESS's async
  stack and shared with the Teams webhook publisher
- Built-in 429 retry with `Retry-After` header respect
- Circuit breaker (same pattern as Datadog PupTool)
- Self-hosted URL construction (`https://{host}/api/0`)

---

### S1.2 — Query issues

List unresolved issues for a Sentry project, filtered to a time window:

```python
    async def query_issues(
        self,
        project: str,
        query: str = "is:unresolved",
        hours_back: int = 1,
    ) -> list[dict]:
        """Query Sentry for issues matching the filter.

        Args:
            project: Sentry project slug (e.g., "auth-service")
            query: Sentry search query (default: unresolved issues)
            hours_back: How far back to look for first-seen issues

        Returns:
            List of issue dicts with id, title, culprit, count, userCount,
            firstSeen, lastSeen, level, status.
        """
        from datetime import datetime, timedelta, timezone
        since = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        params = {
            "query": query,
            "sort": "date",
            "statsPeriod": f"{hours_back}h",
            "project": project,
        }
        return await self._request(
            "GET",
            f"/organizations/{quote(self.org)}/issues/",
            params=params,
        )
```

**For post-deploy monitoring**, ESS will typically call:
```python
issues = await sentry.query_issues(
    project="auth-service",
    query="is:unresolved",
    hours_back=1,  # or minutes since deploy
)
```

This surfaces any new or re-opened issues that may correlate with the deploy.

---

### S1.3 — Issue details

Get full details for a specific issue (stack trace, events, affected users):

```python
    async def get_issue_details(self, issue_id: str) -> dict:
        """Get detailed information about a specific Sentry issue.

        Returns issue metadata, latest event with stack trace, tags,
        and affected user count.
        """
        issue = await self._request("GET", f"/issues/{issue_id}/")
        # Get the latest event for stack trace
        latest_event = await self._request(
            "GET", f"/issues/{issue_id}/events/latest/"
        )
        issue["latest_event"] = latest_event
        return issue
```

The agent uses this when it finds interesting issues during triage and wants to
understand the root cause (stack trace, affected endpoints, error message).

---

### S1.4 — Search traces

Query performance traces for a project:

```python
    async def search_traces(
        self,
        project: str,
        query: str = "",
        hours_back: int = 1,
    ) -> list[dict]:
        """Search Sentry performance traces.

        Useful for correlating latency issues with error patterns.
        """
        params = {
            "query": query,
            "statsPeriod": f"{hours_back}h",
            "project": project,
            "sort": "-timestamp",
            "per_page": 20,
        }
        return await self._request(
            "GET",
            f"/organizations/{quote(self.org)}/events/",
            params=params,
        )
```

---

### S1.5 — ToolResult normalisation

Convert Sentry API responses to the shared `ToolResult` format:

```python
from models import ToolResult

def sentry_issues_to_tool_result(issues: list[dict], duration_ms: int) -> ToolResult:
    """Convert Sentry issues list to normalised ToolResult."""
    if not issues:
        return ToolResult(
            tool="sentry.issues",
            success=True,
            data=[],
            summary="No unresolved Sentry issues found",
            error=None,
            duration_ms=duration_ms,
            raw={},
        )

    summaries = []
    for issue in issues[:5]:  # summarise top 5
        summaries.append(
            f"[{issue.get('level', 'error')}] {issue.get('title', 'Unknown')} "
            f"(seen {issue.get('count', '?')}x, {issue.get('userCount', '?')} users)"
        )

    return ToolResult(
        tool="sentry.issues",
        success=True,
        data=issues,
        summary=f"{len(issues)} issue(s) found: " + "; ".join(summaries),
        error=None,
        duration_ms=duration_ms,
        raw={},
    )


def sentry_issue_detail_to_tool_result(issue: dict, duration_ms: int) -> ToolResult:
    """Convert Sentry issue details to normalised ToolResult."""
    title = issue.get("title", "Unknown")
    culprit = issue.get("culprit", "Unknown")
    count = issue.get("count", "?")
    users = issue.get("userCount", "?")

    # Extract stack trace from latest event if present
    stack_frames = []
    event = issue.get("latest_event", {})
    for entry in event.get("entries", []):
        if entry.get("type") == "exception":
            for exc in entry.get("data", {}).get("values", []):
                for frame in exc.get("stacktrace", {}).get("frames", [])[-3:]:
                    stack_frames.append(
                        f"  {frame.get('filename', '?')}:{frame.get('lineNo', '?')} "
                        f"in {frame.get('function', '?')}"
                    )

    stack_str = "\n".join(stack_frames) if stack_frames else "No stack trace"

    return ToolResult(
        tool="sentry.issue_details",
        success=True,
        data=issue,
        summary=(
            f"{title} | {culprit} | {count}x occurrences, {users} users\n"
            f"Stack (top 3 frames):\n{stack_str}"
        ),
        error=None,
        duration_ms=duration_ms,
        raw={},
    )
```

---

### S2.1 — Auth configuration

ESSConfig fields for Sentry:

```python
# In config.py (ESSConfig)
sentry_auth_token: str          # SENTRY_AUTH_TOKEN env var
sentry_host: str = "sentry.example.com"  # Self-hosted Sentry URL
sentry_org: str = "example"     # Organisation slug
```

The `SentryClient.__init__` constructs the base URL from `sentry_host` and
attaches the auth token as a `Bearer` header. No OAuth flow needed — Sentry auth
tokens are long-lived.

**Self-hosted considerations**:
- The base URL is `https://{sentry_host}/api/0` — no SaaS-specific routes
- The auth token must have `project:read`, `event:read`, `issue:read` scopes
- If using internal integrations, the token format is the same

---

### S2.2 — Rate limiting

Sentry enforces per-org rate limits. The client handles this:

1. **429 responses**: Back off using the `Retry-After` header value, retry up to
   3 times before failing the call
2. **Concurrent limit**: No explicit semaphore needed — Sentry calls are
   sequential within a health-check cycle (query issues → get details for
   interesting ones). If multiple monitoring sessions run concurrently, the 429
   handling provides natural backpressure.
3. **Pagination**: For v1, limit query results to the first page (default 25
   issues). ESS doesn't need to page through thousands of issues — the top
   unresolved issues since deploy time are sufficient.

---

### S2.3 — Circuit breaker

Same pattern as Datadog:
- 3 consecutive API failures → circuit open
- All subsequent calls return immediately with an error ToolResult
- The agent notes "Sentry tools unavailable" in the health report
- Circuit is per `SentryClient` instance (one per ESS process)

---

### S2.4 — Integration test

```python
@pytest.mark.integration
async def test_sentry_query_issues_real():
    """Query Sentry for issues — requires SENTRY_AUTH_TOKEN."""
    client = SentryClient(config=load_test_config())
    issues = await client.query_issues("auth-service", hours_back=24)
    assert isinstance(issues, list)
    await client.close()

@pytest.mark.integration
async def test_sentry_issue_details_real():
    """Get issue details — requires SENTRY_AUTH_TOKEN and a known issue ID."""
    client = SentryClient(config=load_test_config())
    issues = await client.query_issues("auth-service", hours_back=24)
    if issues:
        detail = await client.get_issue_details(str(issues[0]["id"]))
        assert "title" in detail
    await client.close()
```

---

### S3.1 — Bedrock tool schemas

```python
SENTRY_TOOLS = [
    {
        "toolSpec": {
            "name": "sentry_query_issues",
            "description": (
                "Query Sentry for unresolved issues in a project. Returns issues "
                "sorted by date with title, culprit, occurrence count, and affected "
                "user count. Use this on every triage cycle to detect new errors "
                "after a deploy."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "project": {
                            "type": "string",
                            "description": "Sentry project slug (e.g., 'auth-service')"
                        },
                        "query": {
                            "type": "string",
                            "description": "Sentry search query (default: 'is:unresolved')",
                            "default": "is:unresolved"
                        },
                        "hours_back": {
                            "type": "integer",
                            "description": "How many hours back to look (default: 1)",
                            "default": 1
                        }
                    },
                    "required": ["project"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "sentry_issue_details",
            "description": (
                "Get detailed information about a specific Sentry issue including "
                "the full stack trace, latest event, tags, and affected user count. "
                "Use this during investigation when you find an interesting issue "
                "from sentry_query_issues."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "issue_id": {
                            "type": "string",
                            "description": "Sentry issue ID (numeric string)"
                        }
                    },
                    "required": ["issue_id"]
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "sentry_search_traces",
            "description": (
                "Search Sentry performance traces for a project. Use during "
                "investigation to correlate latency spikes with error patterns. "
                "Returns recent traces sorted by timestamp."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "project": {
                            "type": "string",
                            "description": "Sentry project slug"
                        },
                        "query": {
                            "type": "string",
                            "description": "Trace search query (optional)",
                            "default": ""
                        },
                        "hours_back": {
                            "type": "integer",
                            "description": "How many hours back to look (default: 1)",
                            "default": 1
                        }
                    },
                    "required": ["project"]
                }
            }
        }
    },
]
```

---

### S3.2 — Tool dispatch mapping

```python
SENTRY_DISPATCH = {
    "sentry_query_issues": lambda client, args: client.query_issues(
        args["project"],
        args.get("query", "is:unresolved"),
        args.get("hours_back", 1),
    ),
    "sentry_issue_details": lambda client, args: client.get_issue_details(
        args["issue_id"],
    ),
    "sentry_search_traces": lambda client, args: client.search_traces(
        args["project"],
        args.get("query", ""),
        args.get("hours_back", 1),
    ),
}
```

---

### S3.3 — System prompt fragment

```text
## Sentry Tools (REST API)

You have access to Sentry error tracking through the following tools:

**Triage (run on every cycle):**
- `sentry_query_issues` — List unresolved issues for the project since deploy

**Investigation (run when issues found):**
- `sentry_issue_details` — Get stack trace and event details for a specific issue
- `sentry_search_traces` — Search performance traces to correlate latency/errors

When checking Sentry, use the `sentry_project` from the deploy context. This
maps to the Sentry project slug (e.g., "auth-service"). If a new issue has
`firstSeen` after the deploy timestamp, it is likely caused by the deploy.

IMPORTANT: When you find Sentry issues, check the `firstSeen` timestamp against
the deploy time. Issues that existed before the deploy are pre-existing and
should be noted but not flagged as deploy-caused.
```

---

## Phase S4 — Future: Sentry MCP Server Upgrade

This phase is deferred until ESS v1 is stable. When ready:

### S4.1 — Evaluate

Test `@sentry/mcp-server` with the self-hosted instance:
```bash
npx @sentry/mcp-server --access-token=TOKEN --host=sentry.example.com
```

Verify:
- stdio transport works correctly
- `search_events` and `search_issues` AI-powered search returns useful results
- Self-hosted API compatibility (no SaaS-only features relied upon)

### S4.2 — MCP stdio adapter

If evaluation passes, implement an alternative `SentryMCPClient` that
communicates via JSON-RPC over stdio to the Sentry MCP server subprocess. This
adapter implements the same interface as `SentryClient` but delegates to MCP
tool calls.

### S4.3 — Feature flag

Add a config flag to switch between REST and MCP:
```python
sentry_backend: str = "rest"  # "rest" | "mcp"
```

The tool layer instantiates the correct client based on config, keeping the
orchestrator agnostic to the backend.

---

## Dependencies

| Dependency | Purpose | Notes |
|---|---|---|
| aiohttp | Async HTTP client | Shared with Teams webhook and Log Scout adapter |
| `SENTRY_AUTH_TOKEN` | API auth | Scopes: project:read, event:read, issue:read |
| Self-hosted Sentry | API target | `https://{sentry_host}/api/0` |

**Future (Phase S4):**
| `@sentry/mcp-server` | MCP stdio transport | Requires Node.js in container |

---

## Success Criteria

- [ ] SentryClient connects to self-hosted Sentry and authenticates
- [ ] `query_issues` returns unresolved issues filtered by time window
- [ ] `get_issue_details` returns stack trace and event data
- [ ] `search_traces` returns performance traces
- [ ] 429 rate-limit responses are handled with `Retry-After` backoff
- [ ] Circuit breaker opens after 3 consecutive failures
- [ ] Integration tests pass against real Sentry (when auth token provided)
- [ ] Tool schemas are compatible with Bedrock converse `toolConfig` format
- [ ] ToolResult normalisation produces agent-readable summaries with stack traces
- [ ] System prompt fragment guides the agent on firstSeen vs deploy time logic
