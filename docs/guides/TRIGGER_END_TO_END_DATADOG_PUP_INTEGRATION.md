# Trigger End-to-End Datadog Pup Integration

This guide runs a real ESS deploy trigger against a local development server and
uses the Datadog Pup adapter to execute live triage checks for
`example-well-service` in the `qa` environment.

## Preconditions

- ESS dependencies installed with `uv sync`
- Datadog credentials present in `config/.env`
- ESS server running locally in development mode:

```bash
uv run uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload
```

## Live Service Understanding

Observed on 2026-03-24 before triggering ESS:

- Monitors: no Datadog monitors matched `service:example-well-service,env:qa`
- Error logs: no `status:error` entries in the last 30 minutes
- APM: `example-well-service` is present in `qa` APM stats with `servlet.request`
  traffic
- APM operations seen for the service:
  - `servlet.request` (server)
  - `jakarta_rs.request` (internal)
  - `mysql.query` (client)
  - `okhttp.request` (client)
  - `http.request` (client)
- Infrastructure hosts: no hosts matched `service:example-well-service`

## Trigger Payload

Reusable payload file:

- [docs/examples/triggers/example-well-service-qa-e2e.json](../examples/triggers/example-well-service-qa-e2e.json)

The payload uses a short monitoring window so the first real Pup-backed check
fires one minute after the trigger is accepted:

- `window_minutes = 2`
- `check_interval_minutes = 1`

## Fire the Trigger

```bash
curl -sS -X POST http://127.0.0.1:8080/api/v1/deploy \
  -H 'Content-Type: application/json' \
  --data @docs/examples/triggers/example-well-service-qa-e2e.json
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

## Watch the Local ESS Logs

Because the server is running with `uvicorn --reload`, the terminal running ESS
will show the live events when the trigger is posted and when the first health
check executes.

Look for log events like:

- `deploy_trigger_accepted`
- `Monitoring session scheduled`
- `pup_ok` or `pup_non_zero_exit`
- `health_check_completed`

## Poll the Job

Replace `<job_id>` with the value returned by the POST call.

```bash
curl -sS http://127.0.0.1:8080/api/v1/deploy/<job_id>
curl -sS http://127.0.0.1:8080/api/v1/status
```

`GET /api/v1/deploy/<job_id>` now includes `latest_result`, which exposes the
most recent Datadog-backed `HealthCheckResult` from the running session.

Useful fields in `latest_result`:

- `cycle_number`
- `overall_severity`
- `findings[]`
- `raw_tool_outputs`

Useful timing:

- Immediately after POST: status should be `scheduled`
- After ~60 seconds: first triage cycle should have run
- After ~120 seconds: monitoring window should complete

Example quick inspection:

```bash
curl -sS http://127.0.0.1:8080/api/v1/deploy/<job_id> | jq '.latest_result'
```

## What This Currently Exercises

This end-to-end flow now executes real Datadog triage checks from ESS:

- `get_monitor_status(service, env)`
- `search_error_logs(service)`
- `get_apm_stats(service, env)`

The completion callback is still a stub, so this does not yet send Teams
notifications or invoke the Phase 3 orchestration/tool-use loop.

## Re-run Checklist

1. Confirm ESS is running locally
2. Confirm `config/.env` has valid Datadog credentials and `DD_SITE=datadoghq.com`
3. POST the trigger payload
4. Watch the ESS terminal for Pup-backed log events
5. Poll the session endpoint until the first check completes