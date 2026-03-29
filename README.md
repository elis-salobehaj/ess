# ESS — Eye of Sauron Service

> *"One service to watch them all, one service to find them,
> one service to alert them all, and in the Teams channel bind them."*

Agentic AI post-deploy monitoring service. ESS watches production deployments
in real time using Datadog, Sentry, and log search tools — orchestrated by an
LLM reasoning loop — and escalates to MS Teams when issues are detected.

Current runtime note: the shipped path is Datadog-first Bedrock monitoring with
deterministic Pup fallback, release-aware Sentry follow-up for degraded
Sentry-enabled services, optional local debug artifacts under
`_local_observability/`, config-gated Teams delivery, and a checked-in
`ess-harness` CLI for repeatable `live` and `degraded` validation runs.
Log Scout and fuller multi-tool orchestration remain future work.

## How It Works

1. **GitLab pipeline** completes a production deploy
2. **ESS receives** a deploy trigger with service metadata
3. **Scheduler ticks** run repeated health checks for a configurable window
4. **Bedrock tool loop** calls Pup-backed Datadog tools and can deepen within a cycle
5. **If Datadog degrades**, ESS performs release-aware Sentry follow-up for the affected Sentry-enabled services
6. **If the LLM path fails**, deterministic Datadog triage still preserves the monitoring window
7. **If Teams is enabled**, ESS posts warning, critical, and summary notifications

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
- The health-check runtime is Datadog-first and now adds release-aware Sentry follow-up using project details, release details, new release issue groups, and top issue details.
- Bedrock auth uses native `AWS_BEARER_TOKEN_BEDROCK` support through botocore; ESS no longer decodes bearer tokens into raw AWS key/secret pairs.
- The current agent runtime uses Claude Sonnet 4.6 for both triage and investigation turns.
- When `ESS_DEBUG_TRACE_ENABLED=true`, ESS writes session-scoped traces and shared debug logs under `_local_observability/`.
- The `ess-harness` CLI supports `live` runs against an existing local ESS instance and `degraded` runs that force the Datadog-to-Sentry path with a temporary local server.
- Log Scout, broader Bedrock-level orchestration, and Teams retry/backoff remain future work.

## Harness Tooling

Use the checked-in harness when you want a repeatable local validation path
without hand-driving curl calls and polling.

```bash
uv run ess-harness

uv run ess-harness live \
  --trigger docs/examples/triggers/example-service-e2e.json

uv run ess-harness degraded \
  --trigger _local_observability/triggers/pason-well-service-qa-degraded-e2e.json
```

- `live` posts a trigger to an already running ESS instance and waits for the session to finish.
- `degraded` starts a temporary local ESS server and injects deterministic degraded Datadog responses while keeping Bedrock and Sentry live.
- Both commands write status and summary artifacts under `_local_observability/`.

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
- [Development](docs/guides/DEVELOPMENT.md) — commands, workflows, and harness usage
- [Trigger End-to-End Datadog Pup Integration](docs/guides/TRIGGER_END_TO_END_DATADOG_PUP_INTEGRATION.md) — smoke runs, longer-window validation, and harness workflows

## License

[Apache License 2.0](LICENSE)
