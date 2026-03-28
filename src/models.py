"""ESS Pydantic v2 models for the deploy trigger API and health-check results.

All external data entering ESS is validated through these models at the HTTP
boundary.  Internal code must only consume typed model instances — never raw dicts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Environment(StrEnum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"
    QA = "qa"


class Infrastructure(StrEnum):
    K8S = "k8s"
    ECS_FARGATE = "ecs-fargate"
    ECS_EC2 = "ecs-ec2"
    BARE_METAL = "bare-metal"
    VM = "vm"


class HealthSeverity(StrEnum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Deploy trigger — request body models
# ---------------------------------------------------------------------------


class DeploymentInfo(BaseModel):
    """Metadata about the deployment event itself."""

    gitlab_pipeline_id: str = Field(..., min_length=1)
    gitlab_project: str = Field(..., min_length=1)
    commit_sha: str = Field(..., min_length=7, max_length=40)
    deployed_by: str = Field(..., min_length=1)
    deployed_at: datetime
    environment: Environment
    regions: list[str] = Field(default_factory=list)

    @field_validator("commit_sha")
    @classmethod
    def _validate_sha(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", v):
            raise ValueError(f"commit_sha must be a hex string (7-40 chars), got {v!r}")
        return v.lower()

    @field_validator("regions", mode="before")
    @classmethod
    def _validate_regions(cls, v: list[str]) -> list[str]:
        return [r.strip().lower() for r in v if r.strip()]


class ServiceTarget(BaseModel):
    """Configuration for a single service to monitor in this deployment."""

    name: str = Field(..., min_length=1, description="Log service name (e.g. 'hub-ca-auth')")
    datadog_service_name: str = Field(..., min_length=1)
    sentry_project: str | None = None
    sentry_dsn: str | None = None
    infrastructure: Infrastructure = Infrastructure.K8S
    # Which ESS Log Scout agent to query for this service's logs.
    # Falls back to settings.default_log_scout_url when not provided.
    log_search_host: str | None = None

    @field_validator("sentry_dsn")
    @classmethod
    def _validate_sentry_dsn(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("https://"):
            raise ValueError("sentry_dsn must start with https://")
        return v

    @field_validator("infrastructure", mode="before")
    @classmethod
    def _normalise_infrastructure(cls, v: Infrastructure | str) -> Infrastructure | str:
        if isinstance(v, Infrastructure):
            return v
        if isinstance(v, str):
            return v.strip().lower().replace("_", "-").replace(" ", "-")
        return v


class MonitoringConfig(BaseModel):
    """Timing and notification config for this monitoring session."""

    window_minutes: int = Field(default=30, ge=1, le=120)
    check_interval_minutes: int = Field(default=5, ge=1, le=60)
    teams_webhook_url: str | None = None

    @field_validator("teams_webhook_url")
    @classmethod
    def _validate_teams_url(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.startswith("https://"):
                raise ValueError("teams_webhook_url must be an HTTPS URL")
            # Basic safeguard against open-redirect / SSRF via webhook URL.
            allowed_hosts = (
                "outlook.office.com",
                "outlook.office365.com",
                "prod.teams.microsoft.com",
            )
            from urllib.parse import urlparse

            host = urlparse(v).hostname or ""
            if not any(host == h or host.endswith("." + h) for h in allowed_hosts):
                raise ValueError(
                    f"teams_webhook_url host {host!r} is not a recognised MS Teams domain"
                )
        return v

    @model_validator(mode="after")
    def _check_interval_vs_window(self) -> MonitoringConfig:
        if self.check_interval_minutes >= self.window_minutes:
            raise ValueError("check_interval_minutes must be less than window_minutes")
        return self


class DeployTrigger(BaseModel):
    """Root request body for POST /api/v1/deploy."""

    deployment: DeploymentInfo
    services: list[ServiceTarget] = Field(..., min_length=1)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    extra_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("services")
    @classmethod
    def _at_least_one_service(cls, v: list[ServiceTarget]) -> list[ServiceTarget]:
        if not v:
            raise ValueError("At least one service must be provided")
        return v


# ---------------------------------------------------------------------------
# Deploy trigger — response models
# ---------------------------------------------------------------------------


class DeployResponse(BaseModel):
    """202 Accepted response body for POST /api/v1/deploy."""

    job_id: str
    status: str = "scheduled"
    services_monitored: int
    checks_planned: int
    regions: list[str]
    monitoring_window_minutes: int
    check_interval_minutes: int


class JobStatusResponse(BaseModel):
    """Response body for GET /api/v1/deploy/{job_id}."""

    job_id: str
    status: str  # scheduled | running | completed | cancelled | error
    services_monitored: int
    checks_completed: int
    checks_planned: int
    started_at: datetime
    next_check_at: datetime | None
    deploy_context: DeploymentInfo
    latest_result: HealthCheckResult | None = None


class CancelResponse(BaseModel):
    """Response body for DELETE /api/v1/deploy/{job_id}."""

    job_id: str
    status: str = "cancelled"


# ---------------------------------------------------------------------------
# Health-check internal models
# ---------------------------------------------------------------------------


class HealthFinding(BaseModel):
    """A single finding from a health-check cycle (tool result summary)."""

    tool: str  # e.g. "datadog.monitors", "sentry.issues", "logs.search"
    severity: HealthSeverity
    summary: str
    details: str | None = None
    links: list[str] = Field(default_factory=list)


class HealthCheckResult(BaseModel):
    """Aggregated result across all services for one health-check cycle."""

    job_id: str
    cycle_number: int
    checked_at: datetime
    overall_severity: HealthSeverity
    findings: list[HealthFinding] = Field(default_factory=list)
    services_checked: list[str] = Field(default_factory=list)
    raw_tool_outputs: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool adapter shared models (Phase 2)
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    """Normalised output from any tool adapter (Datadog, Sentry, Log Scout).

    All tool adapters return this shape so the orchestrator works with a
    consistent type rather than raw API responses.

    Attributes:
        tool:        Dot-namespaced tool identifier, e.g. ``"datadog.monitors"``.
        success:     ``True`` if the underlying call succeeded.
        data:        Parsed response payload (empty dict on failure).
        summary:     One-line human-readable description of the result.
        error:       Error message when ``success`` is ``False``; ``None`` otherwise.
        duration_ms: Wall-clock time of the underlying call in milliseconds.
        raw:         Original response or subprocess output, kept for debugging.
    """

    tool: str
    success: bool
    data: dict[str, Any]
    summary: str
    error: str | None
    duration_ms: int
    raw: dict[str, Any]
