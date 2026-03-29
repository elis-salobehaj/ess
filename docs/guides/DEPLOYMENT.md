# ESS Deployment Guide

## Runtime Requirements

- Container runtime capable of running the checked-in Docker image
- Network access from ESS to Datadog, Sentry, Microsoft Teams webhooks, and AWS Bedrock
- A writable `_local_observability/` mount if you want local debug traces on disk
- A reverse proxy or ingress that can reach `POST /api/v1/deploy`

## Container Packaging

Build the production image with the checked-in Dockerfile:

```bash
docker build -t ess:prod .
```

Run it directly:

```bash
docker run --rm \
  --env-file config/.env \
  -p 8080:8080 \
  -v "$PWD/_local_observability:/app/_local_observability" \
  ess:prod
```

Or use the checked-in compose file for local or single-node deployment:

```bash
docker compose up --build -d
```

The compose file mounts `_local_observability/` so debug traces and local operator artifacts survive container restarts.

## GitLab Trigger Integration

ESS ships a ready-to-adapt GitLab template in `.gitlab-ci.example.yml`.

Required pipeline variables:

- `ESS_URL`: base URL of the ESS service
- `RELEASE_VERSION`: exact Sentry release tag for the deployed build
- `DD_SERVICE_NAME`: Datadog service identifier used by Pup queries
- `SENTRY_PROJECT`: Sentry project slug when the service is Sentry-enabled
- `SENTRY_PROJECT_ID`: Sentry numeric project ID when the service is Sentry-enabled

Datadog-only services may remove `sentry_project` and `sentry_project_id` from the payload. Sentry-enabled services must provide both fields so ESS can correlate release-aware issue queries correctly.

## Health And Observability Endpoints

- `GET /health`: liveness probe and active-session count
- `GET /api/v1/status`: active monitoring sessions with progress and next run time
- `GET /metrics`: Prometheus text metrics for `ess_active_sessions`, `ess_checks_executed_total`, `ess_alerts_sent_total`, `ess_tool_calls_total`, and `ess_tool_call_duration_ms_total`

When `ESS_DEBUG_TRACE_ENABLED=true`, ESS also writes session-scoped JSONL traces, Markdown digests, and the shared debug log under `_local_observability/`.

## OpenTelemetry Export Path Decision

Phase 5 standardises on OTLP/HTTP to an external OpenTelemetry Collector as the future export path for the Phase 1.5 instrumentation layer. The checked-in runtime continues to keep the JSONL trace sink as a local-only fallback for development and incident replay. This means:

- ESS trace events remain structured and exporter-friendly
- the production deployment shape should route external telemetry through a collector rather than directly from ESS to every backend
- `_local_observability/` remains the supported local fallback when collector export is unavailable or intentionally disabled

## Operational Notes

- `ESS_TEAMS_DELIVERY_MODE=real-world` remains the production default
- real-world mode posts critical alerts immediately, requests early completion after that cycle, and only posts warnings when the monitoring window completes with repeated warnings
- healthy monitoring windows do not post a completion card to Teams in real-world mode
- Pup and Sentry adapters are process-wide rate-limited and circuit-break once their failure thresholds are exceeded