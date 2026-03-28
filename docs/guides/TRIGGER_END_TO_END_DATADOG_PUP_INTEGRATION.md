# Trigger End-to-End Datadog Pup Integration

This guide runs a real ESS deploy trigger against a local development server and
uses the live Bedrock plus Datadog Pup runtime path for
`example-service` in the `qa` environment.

## Preconditions

- ESS dependencies installed with `uv sync`
- Valid Datadog credentials present in `config/.env`
- Valid `AWS_BEARER_TOKEN_BEDROCK` present in `config/.env`
- ESS server running locally in development mode:

```bash
uv run uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload
```

## Trigger Payloads

- Repository fixture: [docs/examples/triggers/example-service-e2e.json](../examples/triggers/example-service-e2e.json)

Longer-window operator payloads are intentionally local-only and should live under `_local_observability/triggers/`, which is gitignored.

The smoke payload uses a 2-minute window so the full session can complete quickly:

- `window_minutes = 2`
- `check_interval_minutes = 1`

## Fire the Smoke Trigger

```bash
curl -sS -X POST http://127.0.0.1:8080/api/v1/deploy \
  -H 'Content-Type: application/json' \
  --data @docs/examples/triggers/example-service-e2e.json
```

Expected response shape:

```json
{
  "job_id": "ess-xxxxxxxx",
  "status": "scheduled",
  "services_monitored": 1,
  "checks_planned": 2,
  "regions": ["qa"],
  "monitoring_window_minutes": 2,
  "check_interval_minutes": 1
}
```

## Watch the Runtime

Look for log events like:

- `deploy_trigger_accepted`
- `Monitoring session scheduled`
- `bedrock_client_initialized`
- `datadog_agent_tool_iteration`
- `teams_notification_delivered` or `teams_notification_failed` when Teams mode is enabled
- `monitoring_session_completed`

For the current native bearer-token runtime, a healthy Bedrock startup line looks like:

- `bedrock_client_initialized ... auth_mode=native_bearer bearer_token_present=true`

If `ESS_DEBUG_TRACE_ENABLED=true`, inspect the local trace file:

```bash
tail -f _local_observability/agent_trace_<job_id>.jsonl
```

For a lower-noise view during development, inspect the companion digest file:

```bash
tail -f _local_observability/agent_trace_digest_<job_id>.md
```

ESS also writes structured debug logs to:

```bash
tail -f _local_observability/ess-debug-logs.log
```

Useful event types:

- `cycle.started`
- `bedrock.request`
- `bedrock.response`
- `tool.use`
- `tool.result`
- `notification.skipped`
- `notification.attempted`
- `notification.delivered`
- `session.completed`

## Poll the Job

Replace `<job_id>` with the value returned by the POST call.

```bash
curl -sS http://127.0.0.1:8080/api/v1/deploy/<job_id>
curl -sS http://127.0.0.1:8080/api/v1/status
```

Useful fields in `latest_result`:

- `cycle_number`
- `overall_severity`
- `findings[]`
- `raw_tool_outputs`

Useful timing for the smoke payload:

- Immediately after POST: status should be `scheduled`
- After ~60 seconds: first Datadog-backed cycle should have run
- After ~120 seconds: session should complete

Useful timing for the 15-minute validation payload:

- Build it from `docs/examples/triggers/example-service-e2e.json` or use a local copy in `_local_observability/triggers/`
- Immediately after POST: status should be `scheduled`
- After ~5 minutes: cycle 1 should complete
- After ~10 minutes: cycle 2 should complete
- After ~15 minutes: cycle 3 and the summary path should complete

Observed local validation status so far:

- The 2-minute smoke payload completed on the live Bedrock path with no fallback.
- A rerun 15-minute payload completed `HEALTHY` on the live Bedrock path across all 3 checks.
- A 30-minute payload also completed `HEALTHY` on the live Bedrock path across all 6 checks.
- The 60-minute window remains available under `_local_observability/triggers/` as an optional longer operator-confidence run.

## What This Exercises

This flow exercises the current Phase 1.5 runtime path:

- Bedrock-driven Datadog tool loop
- Native bearer-token Bedrock auth via `AWS_BEARER_TOKEN_BEDROCK`
- Deterministic Pup fallback when needed
- Session API status inspection
- Debug trace instrumentation when enabled
- Teams gate and notification callbacks when enabled and configured

## Re-run Checklist

1. Confirm ESS is running locally
2. Confirm `config/.env` has valid Datadog and Bedrock credentials
3. Use `docs/examples/triggers/example-service-e2e.json` for smoke tests and keep longer-window variants in `_local_observability/triggers/`
4. POST the trigger payload
5. Watch logs and the optional trace file
6. Poll the session endpoint until the session completes