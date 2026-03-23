# ESS Architecture

## Overview

ESS (Eye of Sauron Service) is an agentic AI post-deploy monitoring service. It
receives deploy notifications from GitLab CI pipelines, runs periodic health
checks using Datadog, Sentry, and log search tools, and escalates findings to
MS Teams.

## System Components

```
┌─────────────────────────────────────────────────────────────────┐
│  ESS — Eye of Sauron Service                                   │
│                                                                 │
│  ┌──────────┐  ┌───────────┐  ┌──────────────┐  ┌───────────┐ │
│  │ Trigger   │  │ Scheduler │  │ AI           │  │ Notifier  │ │
│  │ API       │→│ APScheduler│→│ Orchestrator  │→│ MS Teams  │ │
│  │ FastAPI   │  │           │  │ ReAct Loop   │  │ Webhook   │ │
│  └──────────┘  └───────────┘  └──────┬───────┘  └───────────┘ │
│                                       │                         │
│                          ┌────────────┼────────────┐            │
│                          ▼            ▼            ▼            │
│                   ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│                   │ Datadog  │ │ Sentry   │ │ Log      │       │
│                   │ Pup CLI  │ │ REST API │ │ Scout    │       │
│                   └──────────┘ └──────────┘ └──────────┘       │
└─────────────────────────────────────────────────────────────────┘
         ▲                                          │
         │ POST /api/v1/deploy                      │ HTTP
    ┌────┴─────┐                            ┌───────▼───────┐
    │ GitLab   │                            │ Syslog Server │
    │ Pipeline │                            │ (Log Scout)   │
    └──────────┘                            └───────────────┘
```

## Component Responsibilities

### Trigger API (FastAPI)
- Receives `POST /api/v1/deploy` from GitLab pipelines
- Validates multi-service deploy payloads via pydantic
- Returns `202 Accepted` with job ID and schedule details
- Exposes `/health` and `/api/v1/status` endpoints

### Job Scheduler (APScheduler)
- Creates interval jobs on deploy trigger (every N minutes)
- Auto-removes jobs after monitoring window expires
- Supports cancellation via `DELETE /api/v1/deploy/{job_id}`
- In-memory job store (v1), Redis persistence (future)

### AI Orchestrator (ReAct Loop)
- LLM-driven reasoning loop using AWS Bedrock converse API
- Haiku 4.5 for triage cycles, Sonnet 4.6 for investigation
- Runs health checks across all services in the deploy trigger
- Escalates to deeper investigation when anomalies detected
- Context-window management with summarisation compaction

### Tool Layer
- **Datadog (Pup CLI)**: async subprocess, monitors/logs/APM/incidents/infra
- **Sentry (REST API)**: aiohttp client, issues/details/traces
- **Log Scout (HTTP)**: remote agent on syslog servers, ripgrep search
- All tools normalised to `ToolResult` dataclass

### Notification (MS Teams)
- Incoming webhook with Adaptive Cards
- Three card types: all-clear, issue-detected, monitoring-summary
- Retry with exponential backoff

## Data Flow

1. GitLab pipeline completes → `POST /deploy` with services array
2. Scheduler creates interval job for the monitoring window
3. Each tick: orchestrator runs triage (Haiku) across all services
4. If anomalies: orchestrator switches to investigation (Sonnet)
5. Findings posted to MS Teams as Adaptive Cards
6. End of window: summary card posted, job removed

## Key Design Decisions

- **Observer only**: ESS never takes remediation actions
- **Multi-service triggers**: one deploy can monitor multiple services
- **Per-service tool config**: each service carries its own DD/Sentry/log config
- **Circuit breakers**: tool adapters disable after 3 consecutive failures
- **Bedrock bearer token**: ABSK format decoded at startup for boto3

## Related Documentation

- [Configuration](CONFIGURATION.md) — env vars and config loader
- [Workflows](WORKFLOWS.md) — detailed flow descriptions
- [Technology Decisions](../designs/technology-decisions.md) — tool selection rationale
