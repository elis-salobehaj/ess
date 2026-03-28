# Getting Started with ESS

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker and Docker Compose (for containerised runs)
- Access to: Datadog (API key + app key), Sentry (auth token), AWS Bedrock
- Datadog Pup CLI v0.34+ (see install instructions below)

## Install Pup CLI (Datadog tool)

Pup is a Datadog Labs CLI binary used by ESS to query Datadog via subprocess.
Check the [releases page](https://github.com/datadog-labs/pup/releases) for the
latest version — as of March 2026 it is v0.34.1.

```bash
# Linux / WSL (x86_64) — download and install v0.34.1
mkdir -p /tmp/pup && \
curl -fsSL https://github.com/datadog-labs/pup/releases/download/v0.34.1/pup_0.34.1_Linux_x86_64.tar.gz \
  -o /tmp/pup/pup_0.34.1_Linux_x86_64.tar.gz && \
  tar -xzf /tmp/pup/pup_0.34.1_Linux_x86_64.tar.gz -C /tmp/pup/ && \
  mv /tmp/pup/pup ~/.local/bin/pup && \
  rm -rf /tmp/pup/ && \
  chmod +x ~/.local/bin/pup

# Validate
pup --version
```

Ensure `~/.local/bin` is on your `$PATH`. If not, add to your shell profile:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Local Setup

```bash
# Clone and enter repo
cd /path/to/ess

# Install dependencies
uv sync

# Copy environment config
cp config/.env.example config/.env
# Edit config/.env with your credentials
# Bedrock uses AWS_BEARER_TOKEN_BEDROCK directly; do not add raw AWS access key / secret pairs

# Run the service
uv run uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload

# Run tests
uv run pytest

# Lint and format
uv run ruff check .
uv run ruff format .
```

Optional debug-friendly local setup:

```env
ESS_DEBUG_TRACE_ENABLED=true
ESS_AGENT_TRACE_PATH=_local_observability/agent_trace.jsonl
ESS_TEAMS_ENABLED=false
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

For a realistic local smoke run against the current Datadog-only runtime, use the checked-in trigger fixture instead:

```bash
curl -sS -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8080/api/v1/deploy \
  --data @docs/examples/triggers/example-service-e2e.json
```

If debug tracing is enabled, inspect:

```bash
tail -f _local_observability/ess-debug-logs.log
tail -f _local_observability/agent_trace_digest_<job_id>.md
```

## Docker

```bash
docker compose up --build
```

## Next Steps

- Read [Architecture](../context/ARCHITECTURE.md) for system design
- Read [Configuration](../context/CONFIGURATION.md) for all env vars
- Read [Trigger End-to-End Datadog Pup Integration](TRIGGER_END_TO_END_DATADOG_PUP_INTEGRATION.md) for smoke and extended validation payloads
- Check [plans/active/](../plans/active/) for current work
