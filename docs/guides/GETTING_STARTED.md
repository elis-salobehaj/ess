# Getting Started with ESS

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker and Docker Compose (for containerised runs)
- Access to: Datadog (API key + app key), Sentry (auth token), AWS Bedrock

## Local Setup

```bash
# Clone and enter repo
cd /path/to/ess

# Install dependencies
uv sync

# Copy environment config
cp config/.env.example config/.env
# Edit config/.env with your credentials

# Run the service
uv run uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload

# Run tests
uv run pytest

# Lint and format
uv run ruff check .
uv run ruff format .
```

## First Deploy Trigger

Once ESS is running, send a test deploy trigger:

```bash
curl -s -X POST http://localhost:8080/api/v1/deploy \
  -H "Content-Type: application/json" \
  -d '{
    "deployment": {
      "gitlab_pipeline_id": "test-123",
      "gitlab_project": "test/repo",
      "commit_sha": "abc123",
      "deployed_by": "developer",
      "deployed_at": "2026-03-22T14:30:00Z",
      "environment": "production",
      "regions": ["ca"]
    },
    "services": [
      {
        "name": "test-service",
        "datadog_service_name": "test-svc",
        "sentry_project": "test-project",
        "infrastructure": "k8s"
      }
    ],
    "monitoring": {
      "window_minutes": 10,
      "check_interval_minutes": 2
    }
  }'
```

## Docker

```bash
docker compose up --build
```

## Next Steps

- Read [Architecture](../context/ARCHITECTURE.md) for system design
- Read [Configuration](../context/CONFIGURATION.md) for all env vars
- Check [plans/active/](../plans/active/) for current work
