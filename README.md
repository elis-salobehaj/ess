# ESS — Eye of Sauron Service

> *"One service to watch them all, one service to find them,
> one service to alert them all, and in the Teams channel bind them."*

Agentic AI post-deploy monitoring service. ESS watches production deployments
in real time using Datadog, Sentry, and log search tools — orchestrated by an
LLM reasoning loop — and escalates to MS Teams when issues are detected.

Current runtime note: the live first deliverable is a Datadog-only monitoring
path backed by Bedrock tool calling, deterministic Pup fallback, optional local
debug artifacts under `_local_observability/`, and config-gated Teams delivery.
Sentry and Log Scout remain planned but are not yet wired into the live
monitoring loop.

## How It Works

1. **GitLab pipeline** completes a production deploy
2. **ESS receives** a deploy trigger with service metadata
3. **Scheduler ticks** run repeated health checks for a configurable window
4. **Bedrock tool loop** calls Pup-backed Datadog tools and can deepen within a cycle
5. **If the LLM path fails**, deterministic Datadog triage still preserves the monitoring window
6. **If Teams is enabled**, ESS posts warning, critical, and summary notifications

ESS does **not** remediate. It watches, investigates, and reports.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Runtime | Python 3.14+ |
| Package manager | uv |
| HTTP framework | FastAPI + uvicorn |
| LLM | AWS Bedrock Converse (current runtime: Claude Sonnet 4.6) |
| Datadog | Pup CLI (320+ commands, agent-mode JSON) |
| Sentry | REST API (self-hosted) |
| Log search | ESS Log Scout (remote agent on syslog servers) |
| Scheduler | APScheduler |
| Notifications | MS Teams incoming webhook (Adaptive Cards) |

## Current Runtime

- Deploy triggers, scheduler-driven monitoring windows, session APIs, and the Datadog Pup tool layer are live.
- Bedrock auth uses native `AWS_BEARER_TOKEN_BEDROCK` support through botocore; ESS no longer decodes bearer tokens into raw AWS key/secret pairs.
- The current agent runtime uses Claude Sonnet 4.6 for both triage and investigation turns.
- When `ESS_DEBUG_TRACE_ENABLED=true`, ESS writes session-scoped traces and shared debug logs under `_local_observability/`.
- Sentry, Log Scout, and Teams retry/backoff remain future work.

## Quick Start

```bash
# Install dependencies
uv sync

# Configure
cp config/.env.example config/.env
# Edit config/.env with your credentials

# Run
uv run uvicorn src.main:app --host 0.0.0.0 --port 8080

# Test
uv run pytest
```

See [docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md) for full setup.

## Documentation

- [Agent Operating Manual](AGENTS.md) — for AI coding agents
- [Documentation Index](docs/README.md) — navigation hub
- [Architecture](docs/context/ARCHITECTURE.md) — system design
- [Configuration](docs/context/CONFIGURATION.md) — env vars reference
- [Getting Started](docs/guides/GETTING_STARTED.md) — setup guide
- [Development](docs/guides/DEVELOPMENT.md) — commands and workflows

## License

[Apache License 2.0](LICENSE)
