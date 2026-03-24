"""ESS configuration — pydantic-settings backed by config/.env.

ABSK bearer token decoding (Decision 8) is handled in model_post_init so
that boto3's credential chain picks up the decoded values from os.environ
immediately after settings are instantiated.
"""

from __future__ import annotations

import base64
import os

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ESSConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="config/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # LLM — AWS Bedrock with ABSK bearer token auth
    # -------------------------------------------------------------------------
    llm_provider: str = "bedrock"
    triage_model: str = "global.anthropic.claude-haiku-4-5"
    investigation_model: str = "global.anthropic.claude-sonnet-4-6"
    aws_bedrock_region: str = "us-west-2"
    # ABSK<Base64(key_id:secret)> — decoded into os.environ in model_post_init
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
    default_teams_webhook_url: str | None = None

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
        """Decode the ABSK bearer token and push credentials into os.environ."""
        token = self.aws_bearer_token_bedrock
        if token:
            payload = token[4:] if token.startswith("ABSK") else token
            try:
                decoded = base64.b64decode(payload).decode("utf-8")
                if ":" in decoded:
                    key_id, secret = decoded.split(":", 1)
                    os.environ["AWS_ACCESS_KEY_ID"] = key_id
                    os.environ["AWS_SECRET_ACCESS_KEY"] = secret.strip()
            except Exception:
                # Credentials will fail at call time — validation happens there.
                pass

        if self.aws_bedrock_region:
            os.environ["AWS_DEFAULT_REGION"] = self.aws_bedrock_region

        if self.aws_ec2_metadata_disabled:
            os.environ["AWS_EC2_METADATA_DISABLED"] = "true"


# Module-level singleton — import this throughout the application.
settings = ESSConfig()
