# ESS — Eye of Sauron Service
# Multi-stage build: builder installs deps with uv, runner is a lean image.

# ---------------------------------------------------------------------------
# Stage 1 — builder: install Python deps into a virtual environment
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS builder

# Install uv (fast Python package manager).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy only the files needed to resolve dependencies first (layer caching).
COPY pyproject.toml uv.lock ./

# Sync production deps only — no dev dependencies in the image.
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 2 — pup: download and verify Pup CLI binary
# ---------------------------------------------------------------------------
FROM debian:trixie-slim AS pup-downloader

RUN apt-get update && apt-get install -y --no-install-recommends curl tar ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pin to a specific release; update when upgrading Pup.
ARG PUP_VERSION=0.34.1
RUN curl -fsSL \
    "https://github.com/datadog-labs/pup/releases/download/v${PUP_VERSION}/pup_${PUP_VERSION}_Linux_x86_64.tar.gz" \
    -o /tmp/pup.tar.gz \
    && tar -xzf /tmp/pup.tar.gz -C /tmp/ \
    && install -m 0755 /tmp/pup /usr/local/bin/pup \
    && rm /tmp/pup.tar.gz /tmp/pup

# ---------------------------------------------------------------------------
# Stage 3 — runner: lean production image
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS runner

# Install curl (health checks) and ca-certificates (TLS).
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy uv for runtime use (uv run).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Copy installed virtual environment from builder.
COPY --from=builder /app/.venv /app/.venv

# Copy Pup CLI binary from downloader stage.
COPY --from=pup-downloader /usr/local/bin/pup /usr/local/bin/pup

WORKDIR /app

# Copy application source.
COPY src/ ./src/
COPY pyproject.toml uv.lock ./

# Ensure the app package is installed (editable install used the venv above).
RUN uv sync --frozen --no-dev

# ESS listens on 8080 by default.
EXPOSE 8080

# Non-root user for least-privilege operation.
RUN useradd --system --no-create-home ess
USER ess

# Health check — liveness probe same as the container orchestrator.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
