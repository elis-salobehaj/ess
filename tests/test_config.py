"""Unit tests for the ESSConfig settings loader."""

from __future__ import annotations

import base64
import os

import pytest

from src.config import ESSConfig


class TestConfigDefaults:
    def test_default_models(self) -> None:
        cfg = ESSConfig(dd_api_key="k", dd_app_key="a", sentry_auth_token="s")
        assert cfg.triage_model == "global.anthropic.claude-haiku-4-5"
        assert cfg.investigation_model == "global.anthropic.claude-sonnet-4-6"

    def test_default_monitoring_values(self) -> None:
        cfg = ESSConfig()
        assert cfg.default_monitoring_window_minutes == 30
        assert cfg.default_check_interval_minutes == 5
        assert cfg.max_monitoring_window_minutes == 120
        assert cfg.max_concurrent_sessions == 20

    def test_default_pup_concurrency_values(self) -> None:
        cfg = ESSConfig()
        assert cfg.pup_max_concurrent == 10
        assert cfg.pup_default_timeout == 60

    def test_default_teams_webhook_is_none(self) -> None:
        cfg = ESSConfig()
        assert cfg.default_teams_webhook_url is None

    def test_log_level_normalised_to_uppercase(self) -> None:
        cfg = ESSConfig(log_level="debug")
        assert cfg.log_level == "DEBUG"

    def test_invalid_log_level_raises(self) -> None:
        with pytest.raises(ValueError):
            ESSConfig(log_level="VERBOSE")


class TestAbskTokenDecoding:
    def _make_token(self, key_id: str, secret: str) -> str:
        payload = base64.b64encode(f"{key_id}:{secret}".encode()).decode()
        return f"ABSK{payload}"

    def test_valid_absk_token_sets_environ(self) -> None:
        token = self._make_token("AKIATEST", "supersecret")
        # Instantiate config — this triggers model_post_init
        ESSConfig(aws_bearer_token_bedrock=token, aws_ec2_metadata_disabled=False)
        assert os.environ.get("AWS_ACCESS_KEY_ID") == "AKIATEST"
        assert os.environ.get("AWS_SECRET_ACCESS_KEY") == "supersecret"

    def test_missing_token_does_not_raise(self) -> None:
        # Should not raise even without a token
        cfg = ESSConfig(aws_bearer_token_bedrock="", aws_ec2_metadata_disabled=False)
        assert cfg.aws_bearer_token_bedrock == ""

    def test_invalid_base64_does_not_raise(self) -> None:
        cfg = ESSConfig(
            aws_bearer_token_bedrock="ABSKnot_valid_base64!!",
            aws_ec2_metadata_disabled=False,
        )
        # Decoding fails silently — credentials will fail at call time.
        assert cfg.aws_bearer_token_bedrock.startswith("ABSK")

    def test_ec2_metadata_disabled_env(self) -> None:
        ESSConfig(aws_ec2_metadata_disabled=True)
        assert os.environ.get("AWS_EC2_METADATA_DISABLED") == "true"

    def test_bedrock_region_propagated_to_environ(self) -> None:
        ESSConfig(aws_bedrock_region="eu-west-1", aws_ec2_metadata_disabled=False)
        assert os.environ.get("AWS_DEFAULT_REGION") == "eu-west-1"
