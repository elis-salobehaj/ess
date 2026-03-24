"""Unit tests for Pydantic v2 model validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    DeploymentInfo,
    DeployTrigger,
    Environment,
    Infrastructure,
    MonitoringConfig,
    ServiceTarget,
    ToolResult,
)


class TestDeploymentInfo:
    def _base(self) -> dict:
        return {
            "gitlab_pipeline_id": "42",
            "gitlab_project": "acme/app",
            "commit_sha": "abc1234",
            "deployed_by": "alice",
            "deployed_at": "2026-03-22T10:00:00Z",
            "environment": "production",
            "regions": ["ca"],
        }

    def test_valid_deployment(self) -> None:
        d = DeploymentInfo.model_validate(self._base())
        assert d.environment == Environment.PRODUCTION
        assert d.commit_sha == "abc1234"  # normalised to lower

    def test_sha_normalised_to_lowercase(self) -> None:
        b = self._base()
        b["commit_sha"] = "ABC1234DEF"
        d = DeploymentInfo.model_validate(b)
        assert d.commit_sha == "abc1234def"

    def test_invalid_sha_raises(self) -> None:
        b = self._base()
        b["commit_sha"] = "not-a-sha!"
        with pytest.raises(ValidationError):
            DeploymentInfo.model_validate(b)

    def test_short_sha_raises(self) -> None:
        b = self._base()
        b["commit_sha"] = "abc12"  # < 7 chars
        with pytest.raises(ValidationError):
            DeploymentInfo.model_validate(b)

    def test_unknown_environment_raises(self) -> None:
        b = self._base()
        b["environment"] = "canary"
        with pytest.raises(ValidationError):
            DeploymentInfo.model_validate(b)

    def test_regions_stripped_and_lowercased(self) -> None:
        b = self._base()
        b["regions"] = ["  CA ", "US"]
        d = DeploymentInfo.model_validate(b)
        assert d.regions == ["ca", "us"]


class TestServiceTarget:
    def test_valid_service(self) -> None:
        s = ServiceTarget.model_validate(
            {"name": "hub-ca-auth", "datadog_service_name": "example-auth"}
        )
        assert s.infrastructure == Infrastructure.K8S  # default

    def test_invalid_sentry_dsn_raises(self) -> None:
        with pytest.raises(ValidationError):
            ServiceTarget.model_validate(
                {
                    "name": "svc",
                    "datadog_service_name": "svc-dd",
                    "sentry_dsn": "http://insecure.example.com",
                }
            )


class TestMonitoringConfig:
    def test_defaults_applied(self) -> None:
        m = MonitoringConfig()
        assert m.window_minutes == 30
        assert m.check_interval_minutes == 5

    def test_interval_gte_window_raises(self) -> None:
        with pytest.raises(ValidationError):
            MonitoringConfig(window_minutes=10, check_interval_minutes=10)

    def test_interval_gt_window_raises(self) -> None:
        with pytest.raises(ValidationError):
            MonitoringConfig(window_minutes=5, check_interval_minutes=10)

    def test_valid_teams_webhook(self) -> None:
        m = MonitoringConfig(teams_webhook_url="https://outlook.office.com/webhook/abc123")
        assert m.teams_webhook_url is not None

    def test_non_https_teams_webhook_raises(self) -> None:
        with pytest.raises(ValidationError):
            MonitoringConfig(teams_webhook_url="http://outlook.office.com/webhook/abc")

    def test_non_teams_domain_webhook_raises(self) -> None:
        with pytest.raises(ValidationError):
            MonitoringConfig(teams_webhook_url="https://evil.example.com/webhook")


class TestDeployTrigger:
    def _base(self) -> dict:
        return {
            "deployment": {
                "gitlab_pipeline_id": "1",
                "gitlab_project": "g/r",
                "commit_sha": "abc1234",
                "deployed_by": "bob",
                "deployed_at": "2026-03-22T12:00:00Z",
                "environment": "staging",
                "regions": [],
            },
            "services": [{"name": "svc-a", "datadog_service_name": "svc-a-dd"}],
        }

    def test_valid_trigger(self) -> None:
        t = DeployTrigger.model_validate(self._base())
        assert len(t.services) == 1
        assert t.monitoring.window_minutes == 30  # default

    def test_empty_services_list_raises(self) -> None:
        b = self._base()
        b["services"] = []
        with pytest.raises(ValidationError):
            DeployTrigger.model_validate(b)

    def test_extra_context_accepted(self) -> None:
        b = self._base()
        b["extra_context"] = {"ticket": "ESS-42", "release_notes": "..."}
        t = DeployTrigger.model_validate(b)
        assert t.extra_context["ticket"] == "ESS-42"


class TestToolResult:
    def test_success_result(self) -> None:
        r = ToolResult(
            tool="datadog.monitors",
            success=True,
            data={"monitors": []},
            summary="0 alerting monitors",
            error=None,
            duration_ms=123,
            raw={"command": "pup monitors list"},
        )
        assert r.tool == "datadog.monitors"
        assert r.success is True
        assert r.error is None

    def test_failure_result(self) -> None:
        r = ToolResult(
            tool="datadog.apm_stats",
            success=False,
            data={},
            summary="Pup CLI failed",
            error="exit code 1: authentication failed",
            duration_ms=50,
            raw={"command": "pup apm services stats svc --env=prod", "stderr": "auth error"},
        )
        assert r.success is False
        assert r.error is not None
        assert r.data == {}
