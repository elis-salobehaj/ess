"""ESS Sentry tool adapter — async REST client for self-hosted Sentry.

This adapter is the REST-first second signal source for ESS. It wraps aiohttp
with:

- a global ``asyncio.Semaphore`` to cap concurrent HTTP calls
- a circuit breaker that opens after N consecutive failures
- explicit request timeouts via ``aiohttp.ClientTimeout``
- bounded 429 retry handling using ``Retry-After`` when available
- pydantic validation of Sentry response payloads before they reach the agent layer
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import quote

import aiohttp
import structlog
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from src.metrics import ESSMetrics

if TYPE_CHECKING:
    from src.config import ESSConfig

logger: structlog.BoundLogger = structlog.get_logger(__name__)  # type: ignore[assignment]

T = TypeVar("T")


class _SentryModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def _coerce_int(value: object) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid integer value")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        return int(stripped)
    raise ValueError(f"Unable to coerce {value!r} to int")


def _coerce_string_list(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("expected a list of strings")
    return [str(item) for item in value]


def _iso8601_utc(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def build_new_release_issue_query(release_version: str, effective_since: datetime) -> str:
    return (
        f'release:"{release_version}" '
        f"firstSeen:>={_iso8601_utc(effective_since)} "
        "is:unresolved issue.category:error"
    )


class SentryProjectDetails(_SentryModel):
    id: int
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    platform: str | None = None
    features: list[str] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def _validate_id(cls, value: object) -> int:
        return _coerce_int(value)

    @field_validator("features", mode="before")
    @classmethod
    def _validate_features(cls, value: object) -> list[str]:
        return _coerce_string_list(value)


class SentryReleaseProject(_SentryModel):
    id: int
    slug: str = Field(min_length=1)
    name: str | None = None
    platform: str | None = None
    has_health_data: bool | None = Field(default=None, alias="hasHealthData")

    @field_validator("id", mode="before")
    @classmethod
    def _validate_id(cls, value: object) -> int:
        return _coerce_int(value)


class SentryReleaseDetails(_SentryModel):
    version: str = Field(min_length=1)
    date_created: datetime = Field(alias="dateCreated")
    last_event: datetime | None = Field(default=None, alias="lastEvent")
    new_groups: int = Field(default=0, alias="newGroups")
    projects: list[SentryReleaseProject] = Field(default_factory=list)

    @field_validator("new_groups", mode="before")
    @classmethod
    def _validate_new_groups(cls, value: object) -> int:
        return _coerce_int(value)

    @field_validator("projects", mode="before")
    @classmethod
    def _validate_projects(cls, value: object) -> list[dict[str, Any]] | list[SentryReleaseProject]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise ValueError("projects must be a list")
        return value


class SentryIssue(_SentryModel):
    id: str = Field(min_length=1)
    title: str = "Unknown"
    culprit: str | None = None
    count: int = 0
    user_count: int = Field(default=0, alias="userCount")
    first_seen: datetime | None = Field(default=None, alias="firstSeen")
    last_seen: datetime | None = Field(default=None, alias="lastSeen")
    level: str | None = None
    status: str | None = None
    permalink: str | None = None

    @field_validator("count", "user_count", mode="before")
    @classmethod
    def _validate_counts(cls, value: object) -> int:
        return _coerce_int(value)


class SentryLatestEvent(_SentryModel):
    id: str | None = None
    event_id: str | None = Field(default=None, alias="eventID")
    title: str | None = None
    message: str | None = None
    culprit: str | None = None
    date_created: datetime | None = Field(default=None, alias="dateCreated")
    entries: list[dict[str, Any]] = Field(default_factory=list)


class SentryIssueDetail(SentryIssue):
    short_id: str | None = Field(default=None, alias="shortId")
    metadata: dict[str, Any] = Field(default_factory=dict)
    latest_event: SentryLatestEvent | None = None


_PROJECT_DETAILS_ADAPTER = TypeAdapter(SentryProjectDetails)
_RELEASE_DETAILS_ADAPTER = TypeAdapter(SentryReleaseDetails)
_ISSUES_ADAPTER = TypeAdapter(list[SentryIssue])


@dataclass
class SentryResult[T]:
    """Raw result from one Sentry REST operation."""

    operation: str
    request_path: str
    status_code: int
    data: T | None
    error: str | None
    duration_ms: int
    raw: dict[str, Any]

    @property
    def success(self) -> bool:
        return self.error is None and self.data is not None


class SentryTool:
    """Execute Sentry REST API calls with validation and bounded async I/O."""

    def __init__(self, config: ESSConfig, metrics: ESSMetrics | None = None) -> None:
        self.config = config
        self.metrics = metrics
        self._semaphore = asyncio.Semaphore(config.sentry_max_concurrent)
        self._consecutive_failures = 0
        self._circuit_open = False
        self._session: aiohttp.ClientSession | None = None

    async def get_project_details(self, project_slug: str) -> SentryResult[SentryProjectDetails]:
        request_path = f"/projects/{quote(self.config.sentry_org)}/{quote(project_slug)}/"
        raw_result = await self._request_json(
            "get_project_details",
            "GET",
            request_path,
        )
        return self._validate_typed_result(raw_result, _PROJECT_DETAILS_ADAPTER)

    async def get_release_details(self, release_version: str) -> SentryResult[SentryReleaseDetails]:
        request_path = (
            f"/organizations/{quote(self.config.sentry_org)}/releases/{quote(release_version)}/"
        )
        raw_result = await self._request_json(
            "get_release_details",
            "GET",
            request_path,
        )
        return self._validate_typed_result(raw_result, _RELEASE_DETAILS_ADAPTER)

    async def query_new_release_issues(
        self,
        project: str | int,
        environment: str,
        release_version: str,
        effective_since: datetime,
        per_page: int = 20,
    ) -> SentryResult[list[SentryIssue]]:
        request_path = f"/organizations/{quote(self.config.sentry_org)}/issues/"
        raw_result = await self._request_json(
            "query_new_release_issues",
            "GET",
            request_path,
            params={
                "project": project,
                "environment": environment,
                "statsPeriod": "30d",
                "sort": "date",
                "per_page": per_page,
                "query": build_new_release_issue_query(release_version, effective_since),
            },
        )
        return self._validate_typed_result(raw_result, _ISSUES_ADAPTER)

    async def get_issue_details(self, issue_id: str) -> SentryResult[SentryIssueDetail]:
        issue_path = f"/issues/{quote(issue_id)}/"
        issue_result = await self._request_json("get_issue_details", "GET", issue_path)
        if not issue_result.success or issue_result.data is None:
            return self._coerce_failure(issue_result)

        latest_event_path = f"/issues/{quote(issue_id)}/events/latest/"
        latest_event_result = await self._request_json(
            "get_issue_details", "GET", latest_event_path
        )
        if not latest_event_result.success or latest_event_result.data is None:
            return SentryResult(
                operation="get_issue_details",
                request_path=issue_path,
                status_code=latest_event_result.status_code,
                data=None,
                error=latest_event_result.error,
                duration_ms=issue_result.duration_ms + latest_event_result.duration_ms,
                raw={
                    "issue": issue_result.raw,
                    "latest_event": latest_event_result.raw,
                },
            )

        combined_payload = dict(issue_result.data)
        combined_payload["latest_event"] = latest_event_result.data

        try:
            detail = SentryIssueDetail.model_validate(combined_payload)
        except ValidationError as exc:
            self._record_failure()
            logger.warning("sentry_response_validation_failed", operation="get_issue_details")
            return SentryResult(
                operation="get_issue_details",
                request_path=issue_path,
                status_code=issue_result.status_code,
                data=None,
                error=str(exc),
                duration_ms=issue_result.duration_ms + latest_event_result.duration_ms,
                raw={
                    "issue": issue_result.raw,
                    "latest_event": latest_event_result.raw,
                },
            )

        return SentryResult(
            operation="get_issue_details",
            request_path=issue_path,
            status_code=issue_result.status_code,
            data=detail,
            error=None,
            duration_ms=issue_result.duration_ms + latest_event_result.duration_ms,
            raw={
                "issue": issue_result.raw,
                "latest_event": latest_event_result.raw,
            },
        )

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.config.sentry_auth_token}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=self.config.sentry_timeout_seconds),
            )
        return self._session

    async def _request_json(
        self,
        operation: str,
        method: str,
        request_path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> SentryResult[dict[str, Any] | list[Any]]:
        if self._circuit_open:
            logger.warning("sentry_circuit_open", operation=operation, request_path=request_path)
            return self._finalize_result(
                SentryResult(
                operation=operation,
                request_path=request_path,
                status_code=0,
                data=None,
                error="Circuit breaker open — Sentry API disabled after consecutive failures",
                duration_ms=0,
                raw={"params": params or {}},
                )
            )

        url = f"{self.config.sentry_base_url()}{request_path}"
        async with self._semaphore:
            session = await self._get_session()

            for attempt in range(1, self.config.sentry_rate_limit_retries + 2):
                started_at = time.monotonic()
                try:
                    async with session.request(method, url, params=params) as response:
                        body_text = await response.text()
                        duration_ms = int((time.monotonic() - started_at) * 1000)
                except (aiohttp.ClientError, TimeoutError) as exc:
                    duration_ms = int((time.monotonic() - started_at) * 1000)
                    self._record_failure()
                    logger.warning(
                        "sentry_request_failed",
                        operation=operation,
                        request_path=request_path,
                        error=str(exc),
                    )
                    return self._finalize_result(
                        SentryResult(
                        operation=operation,
                        request_path=request_path,
                        status_code=0,
                        data=None,
                        error=str(exc),
                        duration_ms=duration_ms,
                        raw={"url": url, "params": params or {}},
                        )
                    )

                raw = {
                    "url": url,
                    "params": params or {},
                    "status_code": response.status,
                    "response_text": body_text[:1000],
                }

                if response.status == 429:
                    if attempt <= self.config.sentry_rate_limit_retries:
                        retry_after = self._retry_after_seconds(response)
                        logger.info(
                            "sentry_rate_limited_retrying",
                            operation=operation,
                            request_path=request_path,
                            attempt=attempt,
                            retry_after_s=retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    self._record_failure()
                    return self._finalize_result(
                        SentryResult(
                        operation=operation,
                        request_path=request_path,
                        status_code=response.status,
                        data=None,
                        error="Sentry API rate-limited after bounded retries",
                        duration_ms=duration_ms,
                        raw=raw,
                        )
                    )

                if response.status >= 400:
                    self._record_failure()
                    return self._finalize_result(
                        SentryResult(
                        operation=operation,
                        request_path=request_path,
                        status_code=response.status,
                        data=None,
                        error=f"Sentry API {response.status}: {body_text[:200]}",
                        duration_ms=duration_ms,
                        raw=raw,
                        )
                    )

                try:
                    payload = json.loads(body_text) if body_text else {}
                except json.JSONDecodeError as exc:
                    self._record_failure()
                    return self._finalize_result(
                        SentryResult(
                        operation=operation,
                        request_path=request_path,
                        status_code=response.status,
                        data=None,
                        error=f"Invalid JSON from Sentry API: {exc}",
                        duration_ms=duration_ms,
                        raw=raw,
                        )
                    )

                self._consecutive_failures = 0
                return self._finalize_result(
                    SentryResult(
                    operation=operation,
                    request_path=request_path,
                    status_code=response.status,
                    data=payload,
                    error=None,
                    duration_ms=duration_ms,
                    raw=raw,
                    )
                )

        return self._finalize_result(
            SentryResult(
            operation=operation,
            request_path=request_path,
            status_code=0,
            data=None,
            error="Sentry request did not complete",
            duration_ms=0,
            raw={"url": url, "params": params or {}},
            )
        )

    def _retry_after_seconds(self, response: aiohttp.ClientResponse) -> int:
        retry_after = response.headers.get("Retry-After", "")
        try:
            parsed = int(retry_after)
        except TypeError, ValueError:
            return self.config.sentry_retry_default_seconds
        return parsed if parsed > 0 else self.config.sentry_retry_default_seconds

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.config.sentry_circuit_breaker_threshold:
            self._circuit_open = True
            logger.error(
                "sentry_circuit_opened",
                consecutive_failures=self._consecutive_failures,
            )

    def _finalize_result(self, result: SentryResult[T]) -> SentryResult[T]:
        if self.metrics is not None:
            self.metrics.record_tool_call("sentry.api", result.duration_ms)
        return result

    def _coerce_failure(self, result: SentryResult[Any]) -> SentryResult[Any]:
        return SentryResult(
            operation=result.operation,
            request_path=result.request_path,
            status_code=result.status_code,
            data=None,
            error=result.error,
            duration_ms=result.duration_ms,
            raw=result.raw,
        )

    def _validate_typed_result(
        self,
        result: SentryResult[dict[str, Any] | list[Any]],
        adapter: TypeAdapter[T],
    ) -> SentryResult[T]:
        if not result.success or result.data is None:
            return self._coerce_failure(result)

        try:
            validated = adapter.validate_python(result.data)
        except ValidationError as exc:
            self._record_failure()
            logger.warning(
                "sentry_response_validation_failed",
                operation=result.operation,
                request_path=result.request_path,
            )
            return SentryResult(
                operation=result.operation,
                request_path=result.request_path,
                status_code=result.status_code,
                data=None,
                error=str(exc),
                duration_ms=result.duration_ms,
                raw=result.raw,
            )

        return SentryResult(
            operation=result.operation,
            request_path=result.request_path,
            status_code=result.status_code,
            data=validated,
            error=None,
            duration_ms=result.duration_ms,
            raw=result.raw,
        )


__all__ = [
    "SentryIssue",
    "SentryIssueDetail",
    "SentryLatestEvent",
    "SentryProjectDetails",
    "SentryReleaseDetails",
    "SentryReleaseProject",
    "SentryResult",
    "SentryTool",
    "build_new_release_issue_query",
]
