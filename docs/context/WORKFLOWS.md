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
6. On window expiry, the job auto-removes and emits an end-of-window summary notification when Teams mode is enabled

## Health-Check Cycle (per tick)

Each scheduler tick runs one health-check cycle across all services:

### Current Runtime Path (Datadog-only)

For each scheduler tick:

1. ESS builds a Datadog-specific Bedrock system prompt and user prompt from the deploy context
2. Bedrock authenticates through botocore's native `AWS_BEARER_TOKEN_BEDROCK` support and can call Datadog tools through the D3 tool layer (`DATADOG_TOOL_CONFIG`)
3. Tool calls are dispatched to `PupTool` and normalised into `ToolResult`
4. The current runtime model is Sonnet 4.6 for both triage and deeper investigation turns, so the same cycle can deepen after the initial triage tool pass
5. When debug tracing is enabled, ESS records prompts, tool uses, tool results, fallback events, notification attempts/outcomes, and cycle completion to the local JSONL sink, writes a companion Markdown digest, and routes structlog output to `_local_observability/ess-debug-logs.log`
6. The resulting findings are stored on the in-memory monitoring session
7. After each successful cycle, ESS evaluates the minimal unattended policy: immediate `CRITICAL`, second consecutive `WARNING`, otherwise no Teams delivery
8. If the Bedrock path fails or returns no tool calls, ESS falls back to deterministic Datadog triage for that cycle

### Target Triage (full multi-tool design)

For each service in the trigger:
1. Check Datadog monitors → any alerting/warning?
2. Search Datadog error logs → new errors since last check?
3. Get APM stats → latency/error-rate regression?
4. Query Sentry issues → new unresolved issues since deploy?
5. Search logs via Log Scout → error patterns in raw logs?

### Investigation (runs if triage finds anomalies)

1. Get Sentry issue details (stack trace, affected users)
2. Search logs for specific error patterns
3. Check APM per-operation breakdown (slow endpoints)
4. Check infrastructure health (host CPU, memory)
5. Correlate: did issues start at deploy time?

### Report

- Severity: `HEALTHY` | `WARNING` | `CRITICAL`
- Per-service findings with evidence
- Deploy-time correlation analysis
- Recommendations (monitor closely, investigate further, consider rollback)

## Escalation Logic

- `HEALTHY`: log and continue
- `WARNING` (2+ consecutive): post warning card to Teams
- `CRITICAL` (any): immediate alert card and include the cycle summary in the Teams payload

## Notification Pipeline

1. Each cycle result is stored on the session object and exposed via `GET /api/v1/deploy/{job_id}`
2. The result callback evaluates the warning/critical policy and resolves the webhook from the trigger payload or default config
3. Delivery attempts and outcomes are emitted through the debug trace instrumentation seam when enabled
4. Cards are POSTed to the Teams webhook with an explicit per-request timeout
5. End-of-window summary uses the same delivery path as cycle notifications
6. Retry with exponential backoff remains Phase 4 work

## Documentation Practices

When completing plan-driven work:
1. Update the plan file frontmatter (check off tasks, update `date_updated`)
2. Update [docs/README.md](../README.md) with current plan status
3. Update context docs if architecture, config, or workflows change
4. Run `review-plan-phase` or equivalent audit before marking phase complete
