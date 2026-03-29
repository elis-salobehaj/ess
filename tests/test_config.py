"""Unit tests for the ESSConfig settings loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.config import ESSConfig


class TestConfigDefaults:
    def test_default_models(self) -> None:
        cfg = ESSConfig(_env_file=None, dd_api_key="k", dd_app_key="a", sentry_auth_token="s")
        assert cfg.triage_model == "global.anthropic.claude-sonnet-4-6"
        assert cfg.investigation_model == "global.anthropic.claude-sonnet-4-6"

    def test_default_monitoring_values(self) -> None:
        cfg = ESSConfig(_env_file=None, dd_api_key="k", dd_app_key="a", sentry_auth_token="s")
        assert cfg.default_monitoring_window_minutes == 30
        assert cfg.default_check_interval_minutes == 5
        assert cfg.max_monitoring_window_minutes == 120
        assert cfg.max_concurrent_sessions == 20

    def test_default_pup_concurrency_values(self) -> None:
        cfg = ESSConfig(_env_file=None, dd_api_key="k", dd_app_key="a", sentry_auth_token="s")
        assert cfg.pup_max_concurrent == 10
        assert cfg.pup_default_timeout == 60

    def test_default_sentry_runtime_values(self) -> None:
        cfg = ESSConfig(_env_file=None, dd_api_key="k", dd_app_key="a", sentry_auth_token="s")
        assert cfg.sentry_timeout_seconds == 30
        assert cfg.sentry_max_concurrent == 5
        assert cfg.sentry_rate_limit_retries == 3
        assert cfg.sentry_retry_default_seconds == 2
        assert cfg.sentry_circuit_breaker_threshold == 3

    def test_default_teams_webhook_is_none(self) -> None:
        cfg = ESSConfig(_env_file=None, dd_api_key="k", dd_app_key="a", sentry_auth_token="s")
        assert cfg.default_teams_webhook_url is None
        assert cfg.teams_timeout_seconds == 10
        assert cfg.teams_delivery_mode == "real-world"
        assert cfg.teams_retry_attempts == 3
        assert cfg.teams_retry_backoff_seconds == 1.0

    def test_phase_1_5_trace_defaults_are_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ESS_TEAMS_ENABLED", raising=False)
        monkeypatch.delenv("ESS_DEBUG_TRACE_ENABLED", raising=False)
        monkeypatch.delenv("ESS_AGENT_TRACE_PATH", raising=False)
        cfg = ESSConfig(_env_file=None, dd_api_key="k", dd_app_key="a", sentry_auth_token="s")
        assert cfg.teams_enabled is False
        assert cfg.debug_trace_enabled is False
        assert cfg.agent_trace_path == Path("_local_observability/agent_trace.jsonl")

    def test_log_level_normalised_to_uppercase(self) -> None:
        cfg = ESSConfig(
            _env_file=None,
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
            log_level="debug",
        )
        assert cfg.log_level == "DEBUG"

    def test_invalid_log_level_raises(self) -> None:
        with pytest.raises(ValueError):
            ESSConfig(
                _env_file=None,
                dd_api_key="k",
                dd_app_key="a",
                sentry_auth_token="s",
                log_level="VERBOSE",
            )

    def test_phase_1_5_prefixed_env_vars_are_honoured(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ESS_TEAMS_ENABLED", "true")
        monkeypatch.setenv("ESS_TEAMS_TIMEOUT_SECONDS", "17")
        monkeypatch.setenv("ESS_TEAMS_DELIVERY_MODE", "all")
        monkeypatch.setenv("ESS_TEAMS_RETRY_ATTEMPTS", "4")
        monkeypatch.setenv("ESS_TEAMS_RETRY_BACKOFF_SECONDS", "2")
        monkeypatch.setenv("ESS_DEBUG_TRACE_ENABLED", "true")
        monkeypatch.setenv("ESS_AGENT_TRACE_PATH", "custom-trace.jsonl")

        cfg = ESSConfig(_env_file=None, dd_api_key="k", dd_app_key="a", sentry_auth_token="s")

        assert cfg.teams_enabled is True
        assert cfg.teams_timeout_seconds == 17
        assert cfg.teams_delivery_mode == "all"
        assert cfg.teams_retry_attempts == 4
        assert cfg.teams_retry_backoff_seconds == 2.0
        assert cfg.debug_trace_enabled is True
        assert cfg.agent_trace_path == Path("custom-trace.jsonl")


class TestBedrockBearerToken:
    def test_bearer_token_is_synced_to_environ(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)

        token = "ABSKexampletoken"
        ESSConfig(
            _env_file=None,
            aws_bearer_token_bedrock=token,
            aws_ec2_metadata_disabled=False,
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
        )

        assert os.environ.get("AWS_BEARER_TOKEN_BEDROCK") == token

    def test_runtime_environment_returns_bedrock_overrides(self) -> None:
        cfg = ESSConfig(
            _env_file=None,
            aws_bearer_token_bedrock="ABSKexampletoken",
            aws_bedrock_region="us-west-2",
            aws_ec2_metadata_disabled=True,
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
        )

        assert cfg.runtime_environment() == {
            "AWS_BEARER_TOKEN_BEDROCK": "ABSKexampletoken",
            "AWS_DEFAULT_REGION": "us-west-2",
            "AWS_EC2_METADATA_DISABLED": "true",
        }

    def test_pup_subprocess_environment_is_config_driven(self) -> None:
        cfg = ESSConfig(
            _env_file=None,
            aws_bearer_token_bedrock="ABSKexampletoken",
            aws_bedrock_region="us-west-2",
            dd_api_key="k",
            dd_app_key="a",
            dd_site="datadoghq.eu",
            sentry_auth_token="s",
        )

        env = cfg.pup_subprocess_environment()

        assert env["AWS_BEARER_TOKEN_BEDROCK"] == "ABSKexampletoken"
        assert env["AWS_DEFAULT_REGION"] == "us-west-2"
        assert env["DD_API_KEY"] == "k"
        assert env["DD_APP_KEY"] == "a"
        assert env["DD_SITE"] == "datadoghq.eu"
        assert env["FORCE_AGENT_MODE"] == "1"

    def test_sentry_base_url_defaults_to_https(self) -> None:
        cfg = ESSConfig(
            _env_file=None,
            sentry_host="sentry.internal.example",
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
        )

        assert cfg.sentry_base_url() == "https://sentry.internal.example/api/0"

    def test_sentry_base_url_preserves_explicit_scheme(self) -> None:
        cfg = ESSConfig(
            _env_file=None,
            sentry_host="https://sentry.internal.example/",
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
        )

        assert cfg.sentry_base_url() == "https://sentry.internal.example/api/0"

    def test_bearer_token_does_not_set_standard_aws_credentials(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

        ESSConfig(
            _env_file=None,
            aws_bearer_token_bedrock="ABSKexampletoken",
            aws_ec2_metadata_disabled=False,
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
        )

        assert os.environ.get("AWS_ACCESS_KEY_ID") is None
        assert os.environ.get("AWS_SECRET_ACCESS_KEY") is None

    def test_missing_token_does_not_raise(self) -> None:
        # Should not raise even without a token
        cfg = ESSConfig(
            _env_file=None,
            aws_bearer_token_bedrock="",
            aws_ec2_metadata_disabled=False,
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
        )
        assert cfg.aws_bearer_token_bedrock == ""

    def test_arbitrary_bearer_token_value_is_accepted(self) -> None:
        cfg = ESSConfig(
            _env_file=None,
            aws_bearer_token_bedrock="ABSKnot_valid_base64!!",
            aws_ec2_metadata_disabled=False,
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
        )
        assert cfg.aws_bearer_token_bedrock.startswith("ABSK")

    def test_ec2_metadata_disabled_env(self) -> None:
        ESSConfig(
            _env_file=None,
            aws_ec2_metadata_disabled=True,
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
        )
        assert os.environ.get("AWS_EC2_METADATA_DISABLED") == "true"

    def test_bedrock_region_propagated_to_environ(self) -> None:
        ESSConfig(
            _env_file=None,
            aws_bedrock_region="eu-west-1",
            aws_ec2_metadata_disabled=False,
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
        )
        assert os.environ.get("AWS_DEFAULT_REGION") == "eu-west-1"
