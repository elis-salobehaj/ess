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
6. On window expiry, job auto-removes and posts summary to Teams

## Health-Check Cycle (per tick)

Each scheduler tick runs one health-check cycle across all services:

### Triage (always runs, Haiku 4.5)

For each service in the trigger:
1. Check Datadog monitors → any alerting/warning?
2. Search Datadog error logs → new errors since last check?
3. Get APM stats → latency/error-rate regression?
4. Query Sentry issues → new unresolved issues since deploy?
5. Search logs via Log Scout → error patterns in raw logs?

### Investigation (runs if triage finds anomalies, Sonnet 4.6)

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
- `CRITICAL` (any): immediate alert card, switch to Sonnet investigation model

## Notification Pipeline

1. Health check produces structured report
2. Report mapped to Adaptive Card template (all-clear, issue, summary)
3. Card POSTed to Teams webhook URL
4. Retry with exponential backoff (3 attempts: 1s, 2s, 4s)
5. On persistent failure: log error, continue monitoring

## Documentation Practices

When completing plan-driven work:
1. Update the plan file frontmatter (check off tasks, update `date_updated`)
2. Update [docs/README.md](../README.md) with current plan status
3. Update context docs if architecture, config, or workflows change
4. Run `review-plan-phase` or equivalent audit before marking phase complete
