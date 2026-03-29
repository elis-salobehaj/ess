# Datadog-Only Unattended and Inspectable Ship

This guide describes the narrowed Phase 1.5 ship path for ESS: Datadog-backed
monitoring with optional local trace inspection and config-gated Teams delivery.

Current validation state:

- The live 2-minute smoke path has been validated against the real Bedrock tool loop with native bearer-token auth.
- A rerun 15-minute validation window completed `HEALTHY` on the real Bedrock path.
- A 30-minute validation window also completed `HEALTHY` on the real Bedrock path.
- The 60-minute window remains available in `_local_observability/triggers/` as an optional longer confidence run.

## Required Configuration

- Datadog: `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE`
- Bedrock: `AWS_BEARER_TOKEN_BEDROCK`, `AWS_BEDROCK_REGION`
- Runtime: `ESS_TEAMS_ENABLED`, `ESS_DEBUG_TRACE_ENABLED`, `ESS_AGENT_TRACE_PATH`
- Optional Teams defaults: `DEFAULT_TEAMS_WEBHOOK_URL`, `ESS_TEAMS_TIMEOUT_SECONDS`

Bedrock auth is passed through as `AWS_BEARER_TOKEN_BEDROCK`; ESS does not decode the token into raw AWS access-key/secret pairs.

## Runtime Modes

### Inspectable Mode

- `ESS_TEAMS_ENABLED=false`
- `ESS_DEBUG_TRACE_ENABLED=false` or `true`
- Session state remains available through `GET /api/v1/deploy/{job_id}`
- When debug tracing is enabled, ESS writes session-scoped JSONL and Markdown trace files under `_local_observability/` and routes structlog output to `_local_observability/ess-debug-logs.log`

### Unattended Mode

- `ESS_TEAMS_ENABLED=true`
- Teams webhook is taken from the trigger payload or `DEFAULT_TEAMS_WEBHOOK_URL`
- Notification policy:
  - immediate `CRITICAL`
  - second consecutive `WARNING`
  - end-of-window summary

## Trace Event Shape

The local JSONL sink uses typed event envelopes aligned with future OpenTelemetry export:

- `cycle.started`
- `bedrock.request`
- `bedrock.response`
- `tool.use`
- `tool.result`
- `fallback.started`
- `fallback.triggered`
- `notification.attempted`
- `notification.delivered`
- `notification.failed`
- `notification.skipped`
- `cycle.completed`
- `session.completed`

Each event includes a trace ID, timestamp, optional cycle number, optional parent event ID, and attributes.

The companion digest file is intentionally lower-noise. It keeps the high-signal milestones only: cycle starts, Bedrock request/response summaries, fallback reasons, tool-result summaries, notification outcomes, cycle findings, and the final session summary.

Checked-in sample trace artifact:

- `docs/examples/triggers/example-service-qa-15m-anonymized-trace.jsonl`

Live local debug artifacts remain under `_local_observability/`, including the
session-scoped JSONL trace, digest, and shared debug log.

## API Surface During the Monitoring Window

- `POST /api/v1/deploy` starts a monitoring session
- `GET /api/v1/deploy/{job_id}` exposes the latest `HealthCheckResult`
- `GET /api/v1/status` lists currently active sessions
- `/health` returns liveness and active-session count

## Runtime Expectations

- Successful Bedrock turns show `bedrock_client_initialized` with `auth_mode=native_bearer` in `_local_observability/ess-debug-logs.log`.
- Successful Datadog tool turns show `datadog_agent_tool_iteration` log events and `bedrock.response` digest entries rather than immediate `agent.fallback` findings.
- Fallback remains intentionally available so the monitoring window still completes if the LLM path fails.

## Example Payloads

- Checked-in smoke fixture: `docs/examples/triggers/example-service-e2e.json`
- Local-only extended-window fixtures: `_local_observability/triggers/`

## Intentionally Out of Scope

- Sentry runtime integration
- Log Scout runtime integration
- Graph or bot-based Teams thread replies beyond the Incoming Webhook transport
- OpenTelemetry exporter destination and collector topology