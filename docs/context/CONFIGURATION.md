# ESS Configuration

## Environment Variables

All configuration is loaded via pydantic-settings from `config/.env`. Import
settings from `src/config.py` — never use raw environment access in
application code.

### LLM — AWS Bedrock

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | No | `bedrock` | Reserved provider selector. Current runtime uses `bedrock`. |
| `TRIAGE_MODEL` | No | `global.anthropic.claude-sonnet-4-6` | Model for triage cycles |
| `INVESTIGATION_MODEL` | No | `global.anthropic.claude-sonnet-4-6` | Model for investigation cycles |
| `AWS_BEDROCK_REGION` | No | `us-west-2` | AWS region for Bedrock |
| `AWS_BEARER_TOKEN_BEDROCK` | Yes | — | ABSK-format bearer token passed through to botocore's native Bedrock auth path |
| `AWS_EC2_METADATA_DISABLED` | No | `true` | Disable IMDS for local dev |

### Datadog (Pup CLI)

| Variable | Required | Default | Description |
|---|---|---|---|
| `DD_API_KEY` | Yes | — | Datadog API key |
| `DD_APP_KEY` | Yes | — | Datadog application key |
| `DD_SITE` | No | `datadoghq.com` | Datadog site base domain used by Pup |
| `PUP_MAX_CONCURRENT` | No | `10` | Max parallel Pup CLI calls |
| `PUP_DEFAULT_TIMEOUT` | No | `60` | Pup subprocess timeout (seconds) |
| `PUP_CIRCUIT_BREAKER_THRESHOLD` | No | `3` | Consecutive Pup failures before the adapter opens its circuit |

### Sentry

| Variable | Required | Default | Description |
|---|---|---|---|
| `SENTRY_AUTH_TOKEN` | Yes | — | Sentry auth token (project:read, event:read, issue:read) |
| `SENTRY_HOST` | No | `sentry.example.com` | Self-hosted Sentry URL |
| `SENTRY_ORG` | No | `example` | Sentry organisation slug |
| `SENTRY_TIMEOUT_SECONDS` | No | `30` | Total timeout for each Sentry HTTP request |
| `SENTRY_MAX_CONCURRENT` | No | `5` | Max parallel Sentry HTTP requests per ESS process |
| `SENTRY_RATE_LIMIT_RETRIES` | No | `3` | Bounded retries for HTTP 429 responses |
| `SENTRY_RETRY_DEFAULT_SECONDS` | No | `2` | Fallback delay when `Retry-After` is missing or invalid |
| `SENTRY_CIRCUIT_BREAKER_THRESHOLD` | No | `3` | Consecutive failures before the Sentry adapter opens its circuit |

`SENTRY_HOST` may be configured as either a bare hostname such as `sentry.example.com`
or a full base URL such as `https://sentry.example.com`. ESS normalises this into the
typed helper `ESSConfig.sentry_base_url()`, which returns the `/api/0` base used by the adapter.

### Log Scout

| Variable | Required | Default | Description |
|---|---|---|---|
| `DEFAULT_LOG_SCOUT_URL` | No | `http://syslog.example.com:8090` | Default log scout endpoint |

### Monitoring Defaults

| Variable | Required | Default | Description |
|---|---|---|---|
| `DEFAULT_MONITORING_WINDOW_MINUTES` | No | `30` | Default monitoring window |
| `DEFAULT_CHECK_INTERVAL_MINUTES` | No | `5` | Default check interval |
| `MAX_MONITORING_WINDOW_MINUTES` | No | `120` | Maximum allowed window |

### MS Teams

| Variable | Required | Default | Description |
|---|---|---|---|
| `ESS_TEAMS_ENABLED` | No | `false` | Enable outbound Teams delivery on the configured runtime path |
| `DEFAULT_TEAMS_WEBHOOK_URL` | No | — | Default Teams webhook URL |
| `ESS_TEAMS_TIMEOUT_SECONDS` | No | `10` | Explicit timeout for each Teams webhook request |
| `ESS_TEAMS_DELIVERY_MODE` | No | `real-world` | Teams delivery policy: `real-world` posts critical alerts immediately, stops the monitoring window after that critical cycle, and defers warnings to completion; `all` posts every ESS card type for review/testing |
| `ESS_TEAMS_RETRY_ATTEMPTS` | No | `3` | Number of retry attempts after the initial Teams webhook failure for retryable errors |
| `ESS_TEAMS_RETRY_BACKOFF_SECONDS` | No | `1` | Base backoff used for retryable Teams webhook failures (`1s`, `2s`, `4s`, ...) |

Notes:

- `real-world` is the default, suppresses the end-of-window summary card in Teams, and ends monitoring early after a critical result.
- With the current Incoming Webhook transport, ESS cannot post investigation follow-ups as true thread replies. Those require Microsoft Graph or a bot transport with a parent message ID.

### Debug Trace

| Variable | Required | Default | Description |
|---|---|---|---|
| `ESS_DEBUG_TRACE_ENABLED` | No | `false` | Enable the local debug JSONL trace sink for observable agent execution events |
| `ESS_AGENT_TRACE_PATH` | No | `_local_observability/agent_trace.jsonl` | Local debug trace template path. Only honoured when `ESS_DEBUG_TRACE_ENABLED=true` |

When debug tracing is enabled, ESS records cycle events, Bedrock request/response metadata, tool uses, tool results, fallback events, notification attempts/outcomes, and session completion events.

ESS also writes a companion human-readable digest next to the JSONL file and routes structlog output to `_local_observability/ess-debug-logs.log`.

Trace files are session-scoped rather than shared across runs. If `ESS_AGENT_TRACE_PATH=_local_observability/agent_trace.jsonl` and the session ID is `ess-6141e715`, ESS writes:

- `_local_observability/agent_trace_ess-6141e715.jsonl`
- `_local_observability/agent_trace_digest_ess-6141e715.md`

ESS creates `_local_observability/` automatically when debug tracing is enabled.

## Config-Owned Runtime Helpers

`ESSConfig` is the only approved boundary between application code and environment-derived runtime state.

- `runtime_environment()` returns the Bedrock/runtime overrides that must be visible to SDKs such as botocore.
- `pup_subprocess_environment()` returns the full environment used for Pup subprocess execution, including Datadog credentials, site selection, and the runtime Bedrock overrides.

Application code should consume these typed helpers rather than reading or mutating environment variables directly.

## ABSK Bearer Token Auth

The `AWS_BEARER_TOKEN_BEDROCK` env var uses the format `ABSK<Base64(key_id:secret)>`.
ESS does not decode that token into raw AWS credentials. Instead, `src/config.py`
syncs the bearer token and Bedrock region settings into the runtime environment so
botocore can use its native Bedrock bearer-token support.

All environment-derived runtime or subprocess state must be routed through typed
helpers on `ESSConfig`, rather than direct `os.environ` access in application code.

## Config Loader Location

`src/config.py` — pydantic-settings `BaseSettings` subclass `ESSConfig`.
