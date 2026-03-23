"""Shared fixtures and helpers for the ESS test suite."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import ESSConfig
from src.main import create_app


@pytest.fixture
def minimal_config() -> ESSConfig:
    """A minimal ESSConfig that uses no real credentials.

    Bypasses the env-file lookup by passing everything directly.
    """
    return ESSConfig(
        aws_bearer_token_bedrock="",
        aws_bedrock_region="us-east-1",
        aws_ec2_metadata_disabled=False,
        dd_api_key="test-dd-key",
        dd_app_key="test-dd-app-key",
        sentry_auth_token="test-sentry-token",
    )


@pytest.fixture
def app(minimal_config: ESSConfig):
    """FastAPI test application with isolated config and scheduler."""
    return create_app(config=minimal_config)


@pytest.fixture
async def client(app) -> AsyncClient:  # type: ignore[type-arg]
    """Async HTTP test client bound to the test app."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
def valid_deploy_payload() -> dict:
    """A valid POST /api/v1/deploy request body."""
    return {
        "deployment": {
            "gitlab_pipeline_id": "99999",
            "gitlab_project": "group/repo",
            "commit_sha": "abc1234def5678",
            "deployed_by": "jane.doe",
            "deployed_at": "2026-03-22T14:30:00Z",
            "environment": "production",
            "regions": ["ca"],
        },
        "services": [
            {
                "name": "hub-ca-auth",
                "datadog_service_name": "pason-auth-service",
                "sentry_project": "auth-service",
                "infrastructure": "k8s",
            }
        ],
        "monitoring": {
            "window_minutes": 10,
            "check_interval_minutes": 5,
        },
    }
