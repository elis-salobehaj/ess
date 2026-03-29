# ESS Workflows

## Deploy Trigger Flow

1. GitLab pipeline completes a production deploy
2. Post-deploy stage sends `POST /api/v1/deploy` to ESS with:
   - Deployment metadata (pipeline ID, SHA, deployer, environment, regions)
   - Services array (each with DD/Sentry/log config)
   - Monitoring settings (window, interval, webhook URL)
3. ESS validates the payload, returns `202 Accepted` with job ID
4. APScheduler creates an interval job for the monitoring window
5. Job ticks every `check_interval_minutes` until `window_minutes` expires
6. On window expiry, the job auto-removes and emits the completion callback; in `real-world` mode ESS only posts a completion card when repeated warnings persisted through the full window

## Health-Check Cycle (per tick)

Each scheduler tick runs one health-check cycle across all services:

### Current Runtime Path (Datadog-first triage with staged Sentry investigation)

For each scheduler tick:

1. ESS builds a Datadog-first Bedrock system prompt and user prompt from the deploy context
2. Bedrock authenticates through botocore's native `AWS_BEARER_TOKEN_BEDROCK` support and can call Datadog tools through the D3 tool layer (`DATADOG_TOOL_CONFIG`)
3. Tool calls are dispatched to `PupTool` and normalised into `ToolResult`
4. If Datadog findings remain healthy, ESS ends the cycle with a Datadog-backed summary and skips Sentry for that cycle
5. If Datadog indicates a warning or critical symptom for a service, ESS starts a deeper investigation loop for that service
6. For Sentry-enabled degraded services, the investigation loop exposes release-aware Sentry tools alongside Datadog investigation tools so the model can correlate release details, new issue groups, issue details, APM operations, and infrastructure evidence in one reasoning path
7. If the investigation loop fails or does not call Sentry tools for a degraded Sentry-enabled service, ESS performs deterministic release-aware Sentry follow-up as a safety rail
8. The current runtime model is Sonnet 4.6 for both triage and deeper investigation turns, so the same cycle can deepen without leaving the Bedrock orchestration seam
9. When debug tracing is enabled, ESS records prompts, Bedrock requests and responses, tool uses, tool results, fallback events, compaction events, notification attempts/outcomes, and cycle completion to the local JSONL sink, writes a companion Markdown digest, and routes structlog output to `_local_observability/ess-debug-logs.log`
10. The resulting findings are stored on the in-memory monitoring session
11. After each successful cycle, ESS evaluates the alert policy: immediate `CRITICAL`, second consecutive `WARNING`, otherwise no alert delivery
12. In `real-world` mode, repeated warnings are deferred until the monitoring window completes; in `all` mode, the repeated-warning card is posted immediately for review/testing
13. In `real-world` mode, a delivered `CRITICAL` result ends the monitoring window after that cycle so ESS does not keep polling once the owning team should already be responding
14. Investigation follow-up replies are not available on the current Incoming Webhook transport because webhook posts do not expose a parent message ID for thread replies
15. If the Bedrock path fails or returns no tool calls during triage, ESS falls back to deterministic Datadog triage for that cycle and still runs Sentry follow-up when the fallback Datadog results are degraded

### Target Triage (full multi-tool design)

For each service in the trigger:
1. Check Datadog monitors → any alerting/warning?
2. Search Datadog error logs → new errors since last check?
3. Get APM stats → latency/error-rate regression?
4. If the triage loop stays healthy, finish the cycle without Sentry work
5. If Datadog is degraded and the service is Sentry-enabled, escalate the same cycle into investigation

### Investigation (runs if triage finds anomalies)

1. Optionally validate Sentry project mapping if the deploy context is suspect
2. Get Sentry project details and release details for the deployed release
3. Query new release issue groups using `effective_since = max(deployed_at, release.dateCreated)`
4. Get Sentry issue details (stack trace, affected users) when issue groups need deeper evidence
5. Check APM per-operation breakdown (slow endpoints)
6. Check infrastructure health (host CPU, memory)
7. Correlate: did issues start after the effective release start?

### Report

- Severity: `HEALTHY` | `WARNING` | `CRITICAL`
- Per-service findings with evidence
- Deploy-time correlation analysis
- Recommendations (monitor closely, investigate further, escalate to the owning team)

## Escalation Logic

- `HEALTHY`: log and continue
- `WARNING` (2+ consecutive): post warning card to Teams
- `CRITICAL` (any): immediate alert card and include the cycle summary in the Teams payload
- `INVESTIGATION`: available only in `all` mode on the current transport; real-world thread replies require a future Graph or bot transport

## Notification Pipeline

1. Each cycle result is stored on the session object and exposed via `GET /api/v1/deploy/{job_id}`
2. The result callback evaluates the warning/critical policy and resolves the webhook from the trigger payload or default config
3. Delivery attempts and outcomes are emitted through the debug trace instrumentation seam when enabled
4. Cards are POSTed to the Teams webhook with an explicit per-request timeout
5. Retryable webhook failures use bounded exponential backoff before ESS records a delivery failure and continues monitoring
6. The default `real-world` mode posts critical alerts immediately, requests early monitoring completion after that critical cycle, defers warning notifications to window completion, and suppresses the completion-summary card in Teams
7. Investigation follow-up thread replies require a future Graph or bot transport because Incoming Webhooks do not expose reply/thread semantics or parent message IDs
8. `all` mode remains available for harness-driven copy review and test-channel validation of every card type

## Self-Observability

- `GET /health` exposes liveness plus the current active-session count
- `GET /api/v1/status` exposes active-session state for operators and harnesses
- `GET /metrics` exposes Prometheus text metrics for active sessions, completed checks, successful Teams alerts, and external tool call durations
- Phase 5 keeps `_local_observability/` as the local fallback sink while standardising future external export on OTLP/HTTP through an OpenTelemetry Collector

## Documentation Practices

When completing plan-driven work:
1. Update the plan file frontmatter (check off tasks, update `date_updated`)
2. Update [docs/README.md](../README.md) with current plan status
3. Update context docs if architecture, config, or workflows change
4. Run `review-plan-phase` or equivalent audit before marking phase complete
