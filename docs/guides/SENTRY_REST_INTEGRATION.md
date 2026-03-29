# Sentry REST Integration

This guide covers the implemented Sentry Phase 2 slice in ESS: the validated
REST adapter, Bedrock-compatible tool layer, shared ToolResult normalisation,
and the current integration-test entry points.

## Why REST First

ESS uses the self-hosted Sentry REST API first because it fits the existing
Python runtime, avoids adding Node.js and stdio lifecycle management, and
already covers the release-aware queries the orchestrator needs next:

- project details for mapping validation
- release details for effective release start
- new release issue groups for deploy-specific triage
- issue details for investigation

The MCP server remains a documented follow-on path once the REST-backed runtime
is stable.

## Required Configuration

Set these values in `config/.env` for the adapter and tool layer:

| Variable | Purpose |
|---|---|
| `SENTRY_AUTH_TOKEN` | Bearer token with `project:read`, `event:read`, and `issue:read` |
| `SENTRY_HOST` | Bare host or full base URL for the self-hosted Sentry instance |
| `SENTRY_ORG` | Organisation slug used in Sentry API paths |
| `SENTRY_TIMEOUT_SECONDS` | Total timeout per request |
| `SENTRY_MAX_CONCURRENT` | Max concurrent Sentry HTTP calls per ESS process |
| `SENTRY_RATE_LIMIT_RETRIES` | Bounded retries for `429 Too Many Requests` |
| `SENTRY_RETRY_DEFAULT_SECONDS` | Fallback delay when `Retry-After` is missing or invalid |
| `SENTRY_CIRCUIT_BREAKER_THRESHOLD` | Consecutive failures before the adapter opens its circuit |

`ESSConfig.sentry_base_url()` normalises `SENTRY_HOST` into the `/api/0` base URL
consumed by the adapter.

## Implemented Tool Surface

The Bedrock-facing Sentry tool layer is implemented in `src/agent/sentry_tools.py`.
The default Bedrock tool config now exposes four tools:

- `sentry_project_details`
- `sentry_release_details`
- `sentry_new_release_issues`
- `sentry_issue_details`

Each tool call follows the same boundary pattern:

1. Bedrock `toolUse` input is validated with pydantic.
2. `SentryTool` executes the REST call with bounded async I/O.
3. Sentry JSON is validated into typed boundary models.
4. The validated result is normalised into the shared `ToolResult` contract.
5. The normalised payload is returned to Bedrock as a `toolResult` message.

This keeps the orchestrator isolated from raw Sentry payload differences.

## Canonical Release-Aware Query

ESS uses this canonical issue filter for release-aware Sentry correlation:

```text
release:"{release_version}" firstSeen:>={effective_since_iso} is:unresolved issue.category:error
```

Where:

- `release_version` comes directly from the deploy trigger and must match the
	SDK release tag exactly
- `effective_since = max(deployment.deployed_at, release.dateCreated)`
- `sentry_project_id` is used for org-scoped issue queries

This is intentionally narrower than a generic unresolved issue search because
older unresolved groups are too noisy for post-deploy triage.

## Timeout, Retry, and Circuit Breaker Behavior

The Sentry adapter enforces ESS async-safety rules:

- `aiohttp.ClientTimeout` bounds each request
- `asyncio.Semaphore` caps concurrent Sentry calls
- `429` responses respect `Retry-After` when present
- retry count is bounded by `SENTRY_RATE_LIMIT_RETRIES`
- consecutive failures open a circuit after `SENTRY_CIRCUIT_BREAKER_THRESHOLD`

When the circuit is open, the adapter returns a failed `SentryResult` immediately
instead of continuing to hit the API.

## ToolResult Contract

Sentry responses are normalised into the shared `ToolResult` structure using:

- `sentry_project_details_to_tool_result`
- `sentry_release_details_to_tool_result`
- `sentry_new_release_issues_to_tool_result`
- `sentry_issue_detail_to_tool_result`

Stable tool names produced by this layer are:

- `sentry.project_details`
- `sentry.release_details`
- `sentry.new_release_issues`
- `sentry.issue_detail`

The normalised result retains:

- a concise summary for Bedrock tool results
- structured `data` for the orchestrator
- `raw` payloads for trace/debug use
- error details on failure paths

## Tests

Implemented coverage includes:

- adapter unit tests in `tests/test_sentry_tool.py`
- Bedrock tool-layer tests in `tests/test_sentry_tools.py`
- config coverage in `tests/test_config.py`

Real-environment integration tests are defined in `tests/test_sentry_tool.py` and
are marked `@pytest.mark.integration`, so they are excluded from the default test run.

To exercise them explicitly, provide:

- `SENTRY_AUTH_TOKEN`
- `SENTRY_HOST`
- `SENTRY_ORG`
- `ESS_TEST_SENTRY_PROJECT`
- `ESS_TEST_SENTRY_PROJECT_ID`
- `ESS_TEST_SENTRY_RELEASE`
- `ESS_TEST_SENTRY_ENVIRONMENT` (optional, defaults to `qa`)

Then run:

```bash
uv run pytest -m integration tests/test_sentry_tool.py
```

## Current Runtime Status

The Sentry REST adapter, release-aware Bedrock tool layer, normalisation seam,
and review-level tests/docs are implemented. The current runtime now performs
deterministic release-aware Sentry follow-up after degraded Datadog findings,
while Datadog remains the source of truth for trace and latency investigation
and fuller Bedrock-level multi-tool orchestration remains Phase 3 work.