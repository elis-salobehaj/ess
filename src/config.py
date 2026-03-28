"""ESS configuration — pydantic-settings backed by config/.env.

Bedrock bearer-token auth is synced into ``os.environ`` in ``model_post_init``
so botocore can use its native ``AWS_BEARER_TOKEN_BEDROCK`` support.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _REPO_ROOT / "config" / ".env"


class ESSConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # -------------------------------------------------------------------------
    # LLM — AWS Bedrock with ABSK bearer token auth
    # -------------------------------------------------------------------------
    llm_provider: str = "bedrock"
    triage_model: str = "global.anthropic.claude-sonnet-4-6"
    investigation_model: str = "global.anthropic.claude-sonnet-4-6"
    aws_bedrock_region: str = "us-west-2"
    # Passed through to botocore's native Bedrock bearer-token auth path.
    aws_bearer_token_bedrock: str = ""
    aws_ec2_metadata_disabled: bool = True

    # -------------------------------------------------------------------------
    # Datadog (environment variables consumed by the Pup CLI binary)
    # -------------------------------------------------------------------------
    dd_api_key: str
    dd_app_key: str
    dd_site: str = "datadoghq.com"
    pup_max_concurrent: int = 10  # max parallel Pup CLI subprocess calls
    pup_default_timeout: int = 60  # seconds per Pup subprocess call

    # -------------------------------------------------------------------------
    # Sentry
    # -------------------------------------------------------------------------
    sentry_auth_token: str
    sentry_host: str = "sentry.example.com"
    sentry_org: str = "example"

    # -------------------------------------------------------------------------
    # Log Scout (remote syslog-side search agent)
    # -------------------------------------------------------------------------
    default_log_scout_url: str = "http://syslog.example.com:8090"

    # -------------------------------------------------------------------------
    # Monitoring defaults
    # -------------------------------------------------------------------------
    default_monitoring_window_minutes: int = 30
    default_check_interval_minutes: int = 5
    max_monitoring_window_minutes: int = 120
    max_concurrent_sessions: int = 20

    # -------------------------------------------------------------------------
    # MS Teams
    # -------------------------------------------------------------------------
    teams_enabled: bool = Field(default=False, validation_alias="ESS_TEAMS_ENABLED")
    default_teams_webhook_url: str | None = None
    teams_timeout_seconds: int = Field(
        default=10,
        validation_alias="ESS_TEAMS_TIMEOUT_SECONDS",
    )

    # -------------------------------------------------------------------------
    # Debug trace sink (Phase 1.5 bridge toward OpenTelemetry export)
    # -------------------------------------------------------------------------
    debug_trace_enabled: bool = Field(
        default=False,
        validation_alias="ESS_DEBUG_TRACE_ENABLED",
    )
    agent_trace_path: Path = Field(
        default=Path("_local_observability/agent_trace.jsonl"),
        validation_alias="ESS_AGENT_TRACE_PATH",
    )

    # -------------------------------------------------------------------------
    # Server
    # -------------------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v!r}")
        return upper

    def model_post_init(self, __context: object) -> None:
        """Sync config-owned runtime environment overrides into ``os.environ``."""
        for key, value in self.runtime_environment().items():
            os.environ[key] = value

    def runtime_environment(self) -> dict[str, str]:
        """Return environment overrides required by runtime SDK integrations."""
        env: dict[str, str] = {}

        if self.aws_bearer_token_bedrock:
            env["AWS_BEARER_TOKEN_BEDROCK"] = self.aws_bearer_token_bedrock

        if self.aws_bedrock_region:
            env["AWS_DEFAULT_REGION"] = self.aws_bedrock_region

        if self.aws_ec2_metadata_disabled:
            env["AWS_EC2_METADATA_DISABLED"] = "true"

        return env

    def pup_subprocess_environment(self) -> dict[str, str]:
        """Return the full environment for Pup subprocess execution."""
        env = os.environ.copy()
        env.update(self.runtime_environment())
        env.update(
            {
                "DD_API_KEY": self.dd_api_key,
                "DD_APP_KEY": self.dd_app_key,
                "DD_SITE": self.dd_site,
                "FORCE_AGENT_MODE": "1",
            }
        )
        return env


# Module-level singleton — import this throughout the application.
settings = ESSConfig()
