# ESS Architecture

## Overview

ESS (Eye of Sauron Service) is an agentic AI post-deploy monitoring service. It
receives deploy notifications from GitLab CI pipelines, runs periodic health
checks using Datadog, Sentry, and log search tools, and escalates findings to
MS Teams.

## Current Implementation Status

Today, the live runtime path is narrower than the target architecture:

- Deploy triggers, scheduler-driven monitoring windows, session status APIs, and Datadog Pup integration are implemented.
- The health-check path now uses a Datadog-only Bedrock tool-calling loop with deterministic Pup fallback when the LLM path fails.
- Sentry and Log Scout are not yet wired into the runtime monitoring loop.
- MS Teams notification and completion reporting are still stubbed.

This means ESS can already run repeated Datadog-backed checks for the monitoring window, but it is not yet the full multi-tool, notification-complete architecture shown below.

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
- Current runtime path: Datadog-only Bedrock tool loop plus deterministic fallback
- Target path: full LLM-driven reasoning loop using AWS Bedrock converse API
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
- Current runtime path: completion callback is still stubbed; no Teams posts yet

## Data Flow

1. GitLab pipeline completes → `POST /deploy` with services array
2. Scheduler creates interval job for the monitoring window
3. Each tick: Datadog agent loop runs Bedrock tool-calling against Pup-backed Datadog tools
4. If the LLM path fails or returns no tool calls, deterministic Datadog triage still runs
5. Findings are stored in the in-memory monitoring session and exposed by the session API
6. End of window: job is removed; Teams summary remains future work

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
