"""Unit tests for POST /api/v1/deploy and related endpoints."""

from __future__ import annotations

import copy

from httpx import AsyncClient


class TestTriggerEndpoint:
    async def test_valid_trigger_returns_202(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        response = await client.post("/api/v1/deploy", json=valid_deploy_payload)
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "scheduled"
        assert body["services_monitored"] == 1
        assert body["checks_planned"] == 2  # window=10, interval=5
        assert body["regions"] == ["ca"]
        assert body["job_id"].startswith("ess-")

    async def test_response_contains_monitoring_config(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        response = await client.post("/api/v1/deploy", json=valid_deploy_payload)
        body = response.json()
        assert body["monitoring_window_minutes"] == 10
        assert body["check_interval_minutes"] == 5

    async def test_multiple_services_monitored(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        payload = copy.deepcopy(valid_deploy_payload)
        payload["services"].append(
            {
                "name": "hub-ca-auth-scheduler",
                "datadog_service_name": "pason-auth-scheduler",
                "infrastructure": "ecs-fargate",
            }
        )
        response = await client.post("/api/v1/deploy", json=payload)
        assert response.status_code == 202
        assert response.json()["services_monitored"] == 2

    async def test_missing_services_returns_422(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        payload = copy.deepcopy(valid_deploy_payload)
        payload["services"] = []
        response = await client.post("/api/v1/deploy", json=payload)
        assert response.status_code == 422

    async def test_missing_deployment_block_returns_422(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        payload = copy.deepcopy(valid_deploy_payload)
        del payload["deployment"]
        response = await client.post("/api/v1/deploy", json=payload)
        assert response.status_code == 422

    async def test_invalid_environment_returns_422(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        payload = copy.deepcopy(valid_deploy_payload)
        payload["deployment"]["environment"] = "not-a-valid-env"
        response = await client.post("/api/v1/deploy", json=payload)
        assert response.status_code == 422

    async def test_invalid_commit_sha_returns_422(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        payload = copy.deepcopy(valid_deploy_payload)
        payload["deployment"]["commit_sha"] = "not-a-sha!"
        response = await client.post("/api/v1/deploy", json=payload)
        assert response.status_code == 422

    async def test_interval_equal_to_window_returns_422(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        payload = copy.deepcopy(valid_deploy_payload)
        payload["monitoring"]["window_minutes"] = 10
        payload["monitoring"]["check_interval_minutes"] = 10
        response = await client.post("/api/v1/deploy", json=payload)
        assert response.status_code == 422

    async def test_invalid_teams_webhook_url_returns_422(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        payload = copy.deepcopy(valid_deploy_payload)
        payload["monitoring"]["teams_webhook_url"] = "http://evil.example.com/hook"
        response = await client.post("/api/v1/deploy", json=payload)
        assert response.status_code == 422

    async def test_teams_webhook_non_https_returns_422(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        payload = copy.deepcopy(valid_deploy_payload)
        payload["monitoring"]["teams_webhook_url"] = "http://outlook.office.com/webhook/..."
        response = await client.post("/api/v1/deploy", json=payload)
        assert response.status_code == 422


class TestGetSession:
    async def test_get_existing_session(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        resp = await client.post("/api/v1/deploy", json=valid_deploy_payload)
        job_id = resp.json()["job_id"]

        get_resp = await client.get(f"/api/v1/deploy/{job_id}")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["job_id"] == job_id
        assert body["services_monitored"] == 1

    async def test_get_unknown_session_returns_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/deploy/ess-doesnotexist")
        assert response.status_code == 404


class TestCancelSession:
    async def test_cancel_existing_session(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        resp = await client.post("/api/v1/deploy", json=valid_deploy_payload)
        job_id = resp.json()["job_id"]

        cancel_resp = await client.delete(f"/api/v1/deploy/{job_id}")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

    async def test_cancel_unknown_session_returns_404(self, client: AsyncClient) -> None:
        response = await client.delete("/api/v1/deploy/ess-doesnotexist")
        assert response.status_code == 404


class TestHealthAndStatus:
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_status_returns_active_sessions(
        self, client: AsyncClient, valid_deploy_payload: dict
    ) -> None:
        await client.post("/api/v1/deploy", json=valid_deploy_payload)
        response = await client.get("/api/v1/status")
        assert response.status_code == 200
        body = response.json()
        assert body["active_sessions"] >= 1
