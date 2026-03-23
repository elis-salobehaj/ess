# ESS — Eye of Sauron Service

> *"One service to watch them all, one service to find them,
> one service to alert them all, and in the Teams channel bind them."*

Agentic AI post-deploy monitoring service. ESS watches production deployments
in real time using Datadog, Sentry, and log search tools — orchestrated by an
LLM reasoning loop — and escalates to MS Teams when issues are detected.

## How It Works

1. **GitLab pipeline** completes a production deploy
2. **ESS receives** a deploy trigger with service metadata
3. **Health checks** run every few minutes for a configurable window
4. **AI orchestrator** calls Datadog, Sentry, and log search tools
5. **If issues detected**, ESS investigates deeper and posts to MS Teams
6. **End of window**, summary report posted to Teams

ESS does **not** remediate. It watches, investigates, and reports.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Runtime | Python 3.14+ |
| Package manager | uv |
| HTTP framework | FastAPI + uvicorn |
| LLM | AWS Bedrock (Claude Haiku 4.5 / Sonnet 4.6) |
| Datadog | Pup CLI (320+ commands, agent-mode JSON) |
| Sentry | REST API (self-hosted) |
| Log search | ESS Log Scout (remote agent on syslog servers) |
| Scheduler | APScheduler |
| Notifications | MS Teams incoming webhook (Adaptive Cards) |

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
