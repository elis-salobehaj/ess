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
- Checked-in anonymized sample trace: [docs/examples/triggers/example-service-qa-15m-anonymized-trace.jsonl](../examples/triggers/example-service-qa-15m-anonymized-trace.jsonl)

Longer-window operator payloads are intentionally local-only and should live under `_local_observability/triggers/`, which is gitignored.

The smoke payload uses a 2-minute window so the full session can complete quickly:

- `window_minutes = 2`
- `check_interval_minutes = 1`

## Run the Harness CLI

Use the normal harness command when ESS is already running locally and you want
to exercise the same trigger path ESS uses in development and production.

```bash
uv run ess-harness live \
  --trigger docs/examples/triggers/example-service-e2e.json

uv run ess-harness live \
  --trigger _local_observability/triggers/pason-well-service-qa-10m.json
```

If ESS is not already running, the command fails fast and prints the helper
command to start it in development mode.

Running `uv run ess-harness` without a subcommand now prints the CLI help and
available commands.

## Run the Degraded Harness

Use the reusable degraded harness when you need to force the live runtime down
the Datadog-to-Sentry follow-up path for a trigger scenario.

```bash
uv run ess-harness degraded \
  --trigger _local_observability/triggers/pason-well-service-qa-degraded-e2e.json
```

Notes:

- `--trigger` is required; the script fails fast if you do not provide a trigger file
- The anonymized JSONL trace is a checked-in artifact reference, not a degraded harness trigger payload
- Harness timeout defaults scale with the trigger window, so the 10-minute live fixture does not need a manual timeout override
- Datadog responses are injected and intentionally degraded for the run
- Bedrock and Sentry stay live, so this remains a real runtime validation of the release-aware follow-up path
- The degraded command starts its own temporary ESS server on `127.0.0.1:8011` by default and prints the final artifact paths on completion

## Fire the Smoke Trigger Manually

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

For a checked-in reference artifact, inspect the anonymized healthy trace sample:

```bash
less docs/examples/triggers/example-service-qa-15m-anonymized-trace.jsonl
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

Useful timing for the 10-minute local live harness payload:

- Use `_local_observability/triggers/pason-well-service-qa-10m.json`
- Immediately after `uv run ess-harness live --trigger ...`: status should be `scheduled`
- After ~5 minutes: cycle 1 should complete
- After ~10 minutes: cycle 2 and the session summary should complete

Observed local validation status so far:

- The 2-minute smoke payload completed on the live Bedrock path with no fallback.
- A rerun 15-minute payload completed `HEALTHY` on the live Bedrock path across all 3 checks.
- A 30-minute payload also completed `HEALTHY` on the live Bedrock path across all 6 checks.
- The production-path live harness is reusable for local longer-window fixtures such as `_local_observability/triggers/pason-well-service-qa-10m.json`, `_local_observability/triggers/pason-well-service-qa-15m.json`, and `_local_observability/triggers/pason-well-service-qa-30m.json`.
- The reusable degraded harness completed a controlled degraded run that forced live release-aware Sentry follow-up.
- The 60-minute window remains available under `_local_observability/triggers/` as an optional longer operator-confidence run.

## What This Exercises

This flow exercises the current Phase 1.5 runtime path:

- Bedrock-driven Datadog tool loop
- Native bearer-token Bedrock auth via `AWS_BEARER_TOKEN_BEDROCK`
- Deterministic Pup fallback when needed
- Session API status inspection
- Debug trace instrumentation when enabled
- Teams gate and notification callbacks when enabled and configured

The degraded harness additionally exercises:

- Forced degraded Datadog evidence
- Release-aware Sentry project/release/new-issue follow-up on the live runtime path
- Session artifact capture for the controlled degraded branch

## Re-run Checklist

1. Confirm ESS is running locally for `uv run ess-harness live ...`
2. Confirm `config/.env` has valid Datadog and Bedrock credentials
3. Use `docs/examples/triggers/example-service-e2e.json` for smoke tests, `_local_observability/triggers/pason-well-service-qa-10m.json` for a shorter live CLI run, and keep longer-window variants in `_local_observability/triggers/`
4. Use `uv run ess-harness live --trigger ...` or POST the trigger payload manually
5. Watch logs and the optional trace file
6. Use `uv run ess-harness degraded --trigger _local_observability/triggers/pason-well-service-qa-degraded-e2e.json` when you specifically need the forced Datadog-to-Sentry branch