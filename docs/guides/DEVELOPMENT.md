# ESS Development Guide

## Commands

```bash
# Install / sync dependencies
uv sync

# Run the service (development)
uv run uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload

# Run all tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=src --cov-report=term-missing

# Run integration tests only (requires real credentials)
uv run pytest -m integration

# Lint
uv run ruff check .

# Format
uv run ruff format .
```

## Local Validation Loop

```bash
# Start the service
uv run uvicorn src.main:app --host 127.0.0.1 --port 8080 --reload

# Trigger the realistic Datadog smoke session
curl -sS -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8080/api/v1/deploy \
  --data @docs/examples/triggers/example-service-e2e.json

# Watch the current runtime
tail -f _local_observability/ess-debug-logs.log
tail -f _local_observability/agent_trace_digest_<job_id>.md
```

## Project Structure

```
ess/
├── AGENTS.md              # Agent operating manual
├── README.md              # Project overview
├── pyproject.toml         # Python project config
├── config/
│   └── .env.example       # Environment template
├── src/
│   ├── __init__.py
│   ├── main.py            # FastAPI app entry point
│   ├── config.py          # pydantic-settings config loader
│   ├── models.py          # Deploy event schema, health check models
│   ├── scheduler.py       # APScheduler job management
│   ├── llm_client.py      # Bedrock converse client
│   ├── agent/             # Datadog Bedrock tool loop + tracing
│   ├── tools/             # Tool adapters (Datadog today; Sentry/Log Scout later)
│   └── notifications/     # MS Teams publisher
├── tests/
├── docs/
│   ├── README.md          # Documentation index
│   ├── context/           # Architecture, config, workflows
│   ├── designs/           # Design decisions
│   ├── guides/            # Setup and dev guides
│   └── plans/             # Implementation plans
└── .agents/skills/        # Agent skills
```

## Testing Strategy

- **Unit tests**: mock subprocess (Pup CLI), HTTP (Sentry, Log Scout, Teams),
  and LLM responses. Run with `uv run pytest`.
- **Integration tests**: marked with `@pytest.mark.integration`. Require real
  Datadog/Sentry/Bedrock credentials. Run with `uv run pytest -m integration`.
- **End-to-end tests**: full deploy trigger → health check → notification flow
  with mocked external services.

## Logging

ESS uses structured JSON logging. Key fields:
- `job_id`: monitoring session identifier
- `service`: service being checked
- `cycle`: health-check cycle number
- `severity`: HEALTHY, WARNING, CRITICAL

When `ESS_DEBUG_TRACE_ENABLED=true`, ESS also writes:

- `_local_observability/ess-debug-logs.log`
- `_local_observability/agent_trace_<job_id>.jsonl`
- `_local_observability/agent_trace_digest_<job_id>.md`

## Docker Development

```bash
# Build the ESS image (pins Pup CLI version via build arg)
docker build -t ess:dev .

# Override Pup version at build time (default: 0.34.1)
docker build --build-arg PUP_VERSION=0.34.1 -t ess:dev .

# Run the service (pass credentials via env file)
docker run --rm --env-file config/.env -p 8080:8080 ess:dev

# Validate Pup CLI is installed in the image
docker run --rm ess:dev pup --version

# Run tests on the host (the production image excludes tests/ by design)
uv run pytest

# Or with docker compose
docker compose up --build
```

## Plan-Driven Workflow

1. Check [../plans/active/](../plans/active/) for current work
2. Pick a task from the plan's completion checklist
3. Implement, test, and update the plan frontmatter
4. Run `review-plan-phase` before marking a phase complete
5. Update [../README.md](../README.md) with progress
