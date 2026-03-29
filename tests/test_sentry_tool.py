"""Unit and integration tests for the Sentry REST adapter and normalisation helpers."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from src.config import ESSConfig
from src.tools.normalise import (
    sentry_issue_detail_to_tool_result,
    sentry_new_release_issues_to_tool_result,
    sentry_project_details_to_tool_result,
    sentry_release_details_to_tool_result,
)
from src.tools.sentry_tool import (
    SentryIssue,
    SentryIssueDetail,
    SentryProjectDetails,
    SentryReleaseDetails,
    SentryResult,
    SentryTool,
    build_new_release_issue_query,
)


def _cfg() -> ESSConfig:
    return ESSConfig(
        _env_file=None,
        dd_api_key="test-api-key",
        dd_app_key="test-app-key",
        sentry_auth_token="test-sentry-token",
        sentry_host="sentry.example.com",
        sentry_org="example-org",
        sentry_timeout_seconds=15,
        sentry_max_concurrent=4,
        sentry_rate_limit_retries=2,
        sentry_retry_default_seconds=3,
        sentry_circuit_breaker_threshold=3,
    )


def _integration_cfg() -> tuple[ESSConfig, str, int, str, str]:
    token = os.getenv("SENTRY_AUTH_TOKEN")
    host = os.getenv("SENTRY_HOST")
    org = os.getenv("SENTRY_ORG")
    project = os.getenv("ESS_TEST_SENTRY_PROJECT")
    project_id = os.getenv("ESS_TEST_SENTRY_PROJECT_ID")
    release_version = os.getenv("ESS_TEST_SENTRY_RELEASE")
    environment = os.getenv("ESS_TEST_SENTRY_ENVIRONMENT", "qa")
    missing = [
        name
        for name, value in (
            ("SENTRY_AUTH_TOKEN", token),
            ("SENTRY_HOST", host),
            ("SENTRY_ORG", org),
            ("ESS_TEST_SENTRY_PROJECT", project),
            ("ESS_TEST_SENTRY_PROJECT_ID", project_id),
            ("ESS_TEST_SENTRY_RELEASE", release_version),
        )
        if not value
    ]
    if missing:
        pytest.skip(
            "Real Sentry integration tests require: " + ", ".join(missing),
        )

    return (
        ESSConfig(
            _env_file=None,
            dd_api_key="test-api-key",
            dd_app_key="test-app-key",
            sentry_auth_token=token or "",
            sentry_host=host or "",
            sentry_org=org or "",
        ),
        project or "",
        int(project_id or "0"),
        release_version or "",
        environment,
    )


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        payload: dict | list | None = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        if text is not None:
            self._text = text
        else:
            self._text = json.dumps(payload if payload is not None else {})

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def text(self) -> str:
        return self._text


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.closed = False

    def request(self, method: str, url: str, params: dict | None = None):
        self.calls.append({"method": method, "url": url, "params": params})
        if not self.responses:
            raise AssertionError("No more fake Sentry responses configured")

        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


class TestSentryToolRequests:
    @pytest.mark.asyncio
    async def test_get_project_details_validates_and_returns_typed_model(self) -> None:
        tool = SentryTool(_cfg())
        session = _FakeSession(
            [
                _FakeResponse(
                    payload={
                        "id": "47",
                        "slug": "auth-service",
                        "name": "Auth Service",
                        "platform": "python",
                        "features": ["issue-stream", "performance-view"],
                    }
                )
            ]
        )
        tool._get_session = AsyncMock(return_value=session)

        result = await tool.get_project_details("auth-service")

        assert result.success is True
        assert result.data is not None
        assert result.data.id == 47
        assert result.data.slug == "auth-service"
        assert (
            session.calls[0]["url"]
            == "https://sentry.example.com/api/0/projects/example-org/auth-service/"
        )
        assert session.calls[0]["params"] is None

    @pytest.mark.asyncio
    async def test_get_release_details_validates_and_returns_typed_model(self) -> None:
        tool = SentryTool(_cfg())
        session = _FakeSession(
            [
                _FakeResponse(
                    payload={
                        "version": "2.4.6",
                        "dateCreated": "2026-03-26T01:53:14Z",
                        "lastEvent": "2026-03-26T02:01:00Z",
                        "newGroups": "6",
                        "projects": [
                            {
                                "id": "47",
                                "slug": "well-service",
                                "name": "Well Service",
                                "platform": "java",
                                "hasHealthData": False,
                            }
                        ],
                    }
                )
            ]
        )
        tool._get_session = AsyncMock(return_value=session)

        result = await tool.get_release_details("2.4.6")

        assert result.success is True
        assert result.data is not None
        assert result.data.version == "2.4.6"
        assert result.data.new_groups == 6
        assert result.data.projects[0].id == 47
        assert (
            session.calls[0]["url"]
            == "https://sentry.example.com/api/0/organizations/example-org/releases/2.4.6/"
        )
        assert session.calls[0]["params"] is None

    @pytest.mark.asyncio
    async def test_query_new_release_issues_builds_canonical_release_query(self) -> None:
        tool = SentryTool(_cfg())
        session = _FakeSession(
            [
                _FakeResponse(
                    payload=[
                        {
                            "id": "1001",
                            "title": "TypeError in auth flow",
                            "culprit": "auth.login",
                            "count": "7",
                            "userCount": "3",
                            "level": "error",
                            "status": "unresolved",
                        }
                    ]
                )
            ]
        )
        tool._get_session = AsyncMock(return_value=session)
        effective_since = datetime(2026, 3, 26, 1, 53, 14, tzinfo=UTC)

        result = await tool.query_new_release_issues(
            47,
            "qa",
            "2.4.6",
            effective_since,
            per_page=15,
        )

        assert result.success is True
        assert result.data is not None
        assert result.data[0].id == "1001"
        assert session.calls[0]["params"] == {
            "project": 47,
            "environment": "qa",
            "statsPeriod": "30d",
            "sort": "date",
            "per_page": 15,
            "query": (
                'release:"2.4.6" firstSeen:>=2026-03-26T01:53:14Z '
                "is:unresolved issue.category:error"
            ),
        }

    def test_build_new_release_issue_query_uses_utc_timestamp(self) -> None:
        effective_since = datetime(2026, 3, 26, 1, 53, 14, tzinfo=UTC)

        query = build_new_release_issue_query("2.4.6", effective_since)

        assert query == (
            'release:"2.4.6" firstSeen:>=2026-03-26T01:53:14Z is:unresolved issue.category:error'
        )

    @pytest.mark.asyncio
    async def test_get_issue_details_merges_latest_event(self) -> None:
        tool = SentryTool(_cfg())
        session = _FakeSession(
            [
                _FakeResponse(
                    payload={
                        "id": "1001",
                        "shortId": "AUTH-12",
                        "title": "TypeError in auth flow",
                        "culprit": "auth.login",
                        "count": "7",
                        "userCount": "3",
                        "level": "error",
                        "status": "unresolved",
                    }
                ),
                _FakeResponse(
                    payload={
                        "id": "evt-1",
                        "eventID": "evt-1",
                        "message": "NoneType has no attribute foo",
                        "dateCreated": "2026-03-28T10:00:00Z",
                        "entries": [{"type": "exception", "data": {"values": []}}],
                    }
                ),
            ]
        )
        tool._get_session = AsyncMock(return_value=session)

        result = await tool.get_issue_details("1001")

        assert result.success is True
        assert result.data is not None
        assert result.data.short_id == "AUTH-12"
        assert result.data.latest_event is not None
        assert result.data.latest_event.event_id == "evt-1"
        assert len(session.calls) == 2


class TestSentryToolFailureModes:
    @pytest.mark.asyncio
    async def test_rate_limit_retries_then_succeeds(self) -> None:
        tool = SentryTool(_cfg())
        session = _FakeSession(
            [
                _FakeResponse(
                    status=429,
                    payload={"detail": "slow down"},
                    headers={"Retry-After": "4"},
                ),
                _FakeResponse(
                    payload={
                        "id": "47",
                        "slug": "auth-service",
                        "name": "Auth Service",
                        "platform": "python",
                        "features": [],
                    }
                ),
            ]
        )
        tool._get_session = AsyncMock(return_value=session)

        with patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
            result = await tool.get_project_details("auth-service")

        assert result.success is True
        sleep_mock.assert_awaited_once_with(4)
        assert len(session.calls) == 2

    @pytest.mark.asyncio
    async def test_http_client_error_opens_circuit_after_threshold(self) -> None:
        tool = SentryTool(_cfg())
        session = _FakeSession(
            [
                aiohttp.ClientError("boom"),
                aiohttp.ClientError("boom"),
                aiohttp.ClientError("boom"),
            ]
        )
        tool._get_session = AsyncMock(return_value=session)

        for _ in range(3):
            result = await tool.get_project_details("auth-service")
            assert result.success is False

        assert tool._circuit_open is True

        short_circuit = await tool.get_project_details("auth-service")
        assert short_circuit.success is False
        assert "Circuit breaker open" in (short_circuit.error or "")

    @pytest.mark.asyncio
    async def test_timeout_returns_failure_result(self) -> None:
        tool = SentryTool(_cfg())
        session = _FakeSession([TimeoutError("timed out")])
        tool._get_session = AsyncMock(return_value=session)

        result = await tool.get_project_details("auth-service")

        assert result.success is False
        assert "timed out" in (result.error or "")

    @pytest.mark.asyncio
    async def test_invalid_json_returns_failure_result(self) -> None:
        tool = SentryTool(_cfg())
        session = _FakeSession([_FakeResponse(text="not-json")])
        tool._get_session = AsyncMock(return_value=session)

        result = await tool.get_project_details("auth-service")

        assert result.success is False
        assert "Invalid JSON" in (result.error or "")

    @pytest.mark.asyncio
    async def test_validation_failure_returns_error_result(self) -> None:
        tool = SentryTool(_cfg())
        session = _FakeSession([_FakeResponse(payload={"unexpected": "object"})])
        tool._get_session = AsyncMock(return_value=session)

        result = await tool.get_project_details("auth-service")

        assert result.success is False
        assert result.error is not None
        assert "slug" in result.error.lower()


class TestSentryToolSessionFactory:
    @pytest.mark.asyncio
    async def test_get_session_uses_configured_headers_and_timeout(self) -> None:
        tool = SentryTool(_cfg())
        captured: dict[str, object] = {}

        class _SessionSpy:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        with patch("aiohttp.ClientSession", new=_SessionSpy):
            session = await tool._get_session()

        assert captured["headers"] == {
            "Authorization": "Bearer test-sentry-token",
            "Content-Type": "application/json",
        }
        timeout = captured["timeout"]
        assert isinstance(timeout, aiohttp.ClientTimeout)
        assert timeout.total == 15
        assert session is tool._session


class TestSentryNormalisation:
    def test_sentry_project_details_to_tool_result_summarises_results(self) -> None:
        result = SentryResult(
            operation="get_project_details",
            request_path="/projects/example-org/auth-service/",
            status_code=200,
            data=SentryProjectDetails.model_validate(
                {
                    "id": "47",
                    "slug": "auth-service",
                    "name": "Auth Service",
                    "platform": "python",
                    "features": ["issue-stream"],
                }
            ),
            error=None,
            duration_ms=18,
            raw={"url": "https://sentry.example.com/api/0/projects/example-org/auth-service/"},
        )

        tool_result = sentry_project_details_to_tool_result(result)

        assert tool_result.tool == "sentry.project_details"
        assert tool_result.success is True
        assert tool_result.data["slug"] == "auth-service"
        assert "id 47" in tool_result.summary

    def test_sentry_release_details_to_tool_result_summarises_results(self) -> None:
        result = SentryResult(
            operation="get_release_details",
            request_path="/organizations/example-org/releases/2.4.6/",
            status_code=200,
            data=SentryReleaseDetails.model_validate(
                {
                    "version": "2.4.6",
                    "dateCreated": "2026-03-26T01:53:14Z",
                    "lastEvent": "2026-03-26T02:01:00Z",
                    "newGroups": 6,
                    "projects": [{"id": 47, "slug": "well-service"}],
                }
            ),
            error=None,
            duration_ms=19,
            raw={
                "url": "https://sentry.example.com/api/0/organizations/example-org/releases/2.4.6/"
            },
        )

        tool_result = sentry_release_details_to_tool_result(result)

        assert tool_result.tool == "sentry.release_details"
        assert tool_result.success is True
        assert tool_result.data["version"] == "2.4.6"
        assert "6 new group" in tool_result.summary

    def test_sentry_new_release_issues_to_tool_result_summarises_results(self) -> None:
        result = SentryResult(
            operation="query_new_release_issues",
            request_path="/organizations/example-org/issues/",
            status_code=200,
            data=[
                SentryIssue.model_validate(
                    {
                        "id": "1001",
                        "title": "TypeError in auth flow",
                        "count": "7",
                        "userCount": "3",
                        "level": "error",
                    }
                )
            ],
            error=None,
            duration_ms=18,
            raw={
                "params": {
                    "query": (
                        'release:"2.4.6" firstSeen:>=2026-03-26T01:53:14Z '
                        "is:unresolved issue.category:error"
                    )
                }
            },
        )

        tool_result = sentry_new_release_issues_to_tool_result(result)

        assert tool_result.tool == "sentry.new_release_issues"
        assert tool_result.success is True
        assert tool_result.data["items"][0]["title"] == "TypeError in auth flow"
        assert "release 2.4.6" in tool_result.summary

    def test_sentry_issue_detail_to_tool_result_preserves_latest_event(self) -> None:
        result = SentryResult(
            operation="get_issue_details",
            request_path="/issues/1001",
            status_code=200,
            data=SentryIssueDetail.model_validate(
                {
                    "id": "1001",
                    "title": "TypeError in auth flow",
                    "count": "7",
                    "userCount": "3",
                    "latest_event": {"eventID": "evt-1", "message": "boom"},
                }
            ),
            error=None,
            duration_ms=21,
            raw={"issue": {}, "latest_event": {}},
        )

        tool_result = sentry_issue_detail_to_tool_result(result)

        assert tool_result.tool == "sentry.issue_detail"
        assert tool_result.success is True
        assert tool_result.data["latest_event"]["eventID"] == "evt-1"

    def test_sentry_failure_to_tool_result_returns_error_shape(self) -> None:
        result = SentryResult(
            operation="query_new_release_issues",
            request_path="/organizations/example-org/issues/",
            status_code=500,
            data=None,
            error="Sentry API 500: bad things",
            duration_ms=19,
            raw={"response_text": "bad things"},
        )

        tool_result = sentry_new_release_issues_to_tool_result(result)

        assert tool_result.success is False
        assert tool_result.tool == "sentry.new_release_issues"
        assert "Sentry new_release_issues failed" in tool_result.summary


class TestSentryToolIntegration:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_project_details_against_real_sentry(self) -> None:
        config, project, _project_id, _release_version, _environment = _integration_cfg()
        tool = SentryTool(config)

        try:
            result = await tool.get_project_details(project)
        finally:
            await tool.close()

        assert result.success is True
        assert result.data is not None
        assert result.data.slug == project

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_release_details_against_real_sentry(self) -> None:
        config, _project, _project_id, release_version, _environment = _integration_cfg()
        tool = SentryTool(config)

        try:
            result = await tool.get_release_details(release_version)
        finally:
            await tool.close()

        assert result.success is True
        assert result.data is not None
        assert result.data.version == release_version

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_query_new_release_issues_against_real_sentry(self) -> None:
        config, _project, project_id, release_version, environment = _integration_cfg()
        tool = SentryTool(config)

        try:
            release = await tool.get_release_details(release_version)
            assert release.success is True
            assert release.data is not None
            result = await tool.query_new_release_issues(
                project_id,
                environment,
                release_version,
                release.data.date_created,
            )
        finally:
            await tool.close()

        assert result.success is True
        assert isinstance(result.data, list)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_issue_details_against_real_sentry_when_issue_available(self) -> None:
        config, _project, project_id, release_version, environment = _integration_cfg()
        tool = SentryTool(config)

        try:
            release = await tool.get_release_details(release_version)
            assert release.success is True
            assert release.data is not None
            issues = await tool.query_new_release_issues(
                project_id,
                environment,
                release_version,
                release.data.date_created,
            )
            assert issues.success is True
            if not issues.data:
                pytest.skip("No Sentry issues available for issue-detail integration test")

            detail = await tool.get_issue_details(issues.data[0].id)
        finally:
            await tool.close()

        assert detail.success is True
        assert detail.data is not None
        assert detail.data.id == issues.data[0].id
