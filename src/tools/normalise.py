"""ESS tool result normalisation — converts raw adapter outputs to ToolResult.

Every tool adapter (Pup CLI, Sentry REST, Log Scout HTTP) produces a typed raw
result.  This module converts each raw result into the shared ``ToolResult``
format that the AI orchestrator consumes.

Keep each converter function pure (no I/O, no side effects) so they are trivial
to test.
"""

from __future__ import annotations

from typing import Any

from src.models import ToolResult
from src.tools.pup_tool import PupResult
from src.tools.sentry_tool import (
    SentryIssue,
    SentryIssueDetail,
    SentryProjectDetails,
    SentryReleaseDetails,
    SentryResult,
)


def pup_to_tool_result(pup_result: PupResult, tool_name: str) -> ToolResult:
    """Convert a ``PupResult`` to the normalised ``ToolResult`` format.

    Args:
        pup_result: Raw output from a ``PupTool`` execution.
        tool_name:  Short dimension name, e.g. ``"monitor_status"``.
                    Prefixed with ``"datadog."`` in the output ``tool`` field.

    Returns:
        A ``ToolResult`` ready for consumption by the AI orchestrator.
        On failure the result has ``success=False``, empty ``data``, and the
        Pup stderr in ``error``.  On success the Pup JSON payload is in
        ``data``; if Pup returned a JSON list it is wrapped as
        ``{"items": [...]}`` so ``data`` is always a ``dict``.
    """
    qualified = f"datadog.{tool_name}"

    if pup_result.exit_code != 0 or pup_result.data is None:
        return ToolResult(
            tool=qualified,
            success=False,
            data={},
            summary=f"Pup CLI failed: {pup_result.stderr[:200]}",
            error=pup_result.stderr,
            duration_ms=pup_result.duration_ms,
            raw={"command": pup_result.command, "stderr": pup_result.stderr},
        )

    # Normalise data to dict — Pup can return a top-level JSON array.
    raw_data: Any = pup_result.data
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {"items": raw_data}

    # Extract human-readable summary from Pup agent-mode metadata when present.
    summary: str = data.get("summary", "")
    if not summary and "metadata" in data:
        summary = data["metadata"].get("description", "")

    return ToolResult(
        tool=qualified,
        success=True,
        data=data,
        summary=summary or f"Pup {tool_name} returned successfully",
        error=None,
        duration_ms=pup_result.duration_ms,
        raw={"command": pup_result.command},
    )


def sentry_project_details_to_tool_result(
    sentry_result: SentryResult[SentryProjectDetails],
) -> ToolResult:
    """Convert a Sentry project-details result to the shared ``ToolResult`` shape."""
    if not sentry_result.success or sentry_result.data is None:
        return _failed_sentry_tool_result(sentry_result, "project_details")

    project = sentry_result.data
    features = ", ".join(project.features[:4]) if project.features else "no advertised features"
    summary = (
        f"Sentry project '{project.slug}' (id {project.id}) "
        f"on platform {project.platform or 'unknown'} with {features}"
    )
    return ToolResult(
        tool="sentry.project_details",
        success=True,
        data=project.model_dump(mode="json", by_alias=True),
        summary=summary,
        error=None,
        duration_ms=sentry_result.duration_ms,
        raw=sentry_result.raw,
    )


def sentry_release_details_to_tool_result(
    sentry_result: SentryResult[SentryReleaseDetails],
) -> ToolResult:
    """Convert a Sentry release-details result to the shared ``ToolResult`` shape."""
    if not sentry_result.success or sentry_result.data is None:
        return _failed_sentry_tool_result(sentry_result, "release_details")

    release = sentry_result.data
    summary = (
        f"Sentry release '{release.version}' created {release.date_created.isoformat()} "
        f"with {release.new_groups} new group(s) across {len(release.projects)} project(s)"
    )
    return ToolResult(
        tool="sentry.release_details",
        success=True,
        data=release.model_dump(mode="json", by_alias=True),
        summary=summary,
        error=None,
        duration_ms=sentry_result.duration_ms,
        raw=sentry_result.raw,
    )


def sentry_new_release_issues_to_tool_result(
    sentry_result: SentryResult[list[SentryIssue]],
) -> ToolResult:
    """Convert a release-aware Sentry issue query result to the shared shape."""
    if not sentry_result.success or sentry_result.data is None:
        return _failed_sentry_tool_result(sentry_result, "new_release_issues")

    issues = [issue.model_dump(mode="json", by_alias=True) for issue in sentry_result.data]
    release_query = str(sentry_result.raw.get("params", {}).get("query", ""))
    release_label = "the requested release slice"
    if 'release:"' in release_query:
        release_label = release_query.split('release:"', 1)[1].split('"', 1)[0]

    if not issues:
        summary = f"No new unresolved Sentry issue groups found for release {release_label}"
    else:
        highlights = "; ".join(
            f"[{issue.level or 'error'}] {issue.title} ({issue.count}x, {issue.user_count} users)"
            for issue in sentry_result.data[:3]
        )
        summary = (
            f"{len(issues)} new unresolved Sentry issue group(s) found for release "
            f"{release_label}: {highlights}"
        )

    return ToolResult(
        tool="sentry.new_release_issues",
        success=True,
        data={"items": issues},
        summary=summary,
        error=None,
        duration_ms=sentry_result.duration_ms,
        raw=sentry_result.raw,
    )


def sentry_issue_detail_to_tool_result(
    sentry_result: SentryResult[SentryIssueDetail],
) -> ToolResult:
    """Convert a Sentry issue detail result to the shared ``ToolResult`` shape."""
    if not sentry_result.success or sentry_result.data is None:
        return _failed_sentry_tool_result(sentry_result, "issue_detail")

    issue = sentry_result.data
    data = issue.model_dump(mode="json", by_alias=True)
    summary = (
        f"Sentry issue '{issue.title}' affecting {issue.user_count} users "
        f"with {issue.count} event(s)"
    )
    return ToolResult(
        tool="sentry.issue_detail",
        success=True,
        data=data,
        summary=summary,
        error=None,
        duration_ms=sentry_result.duration_ms,
        raw=sentry_result.raw,
    )


def _failed_sentry_tool_result(
    sentry_result: SentryResult[Any],
    tool_name: str,
) -> ToolResult:
    return ToolResult(
        tool=f"sentry.{tool_name}",
        success=False,
        data={},
        summary=f"Sentry {tool_name} failed: {(sentry_result.error or 'unknown error')[:200]}",
        error=sentry_result.error,
        duration_ms=sentry_result.duration_ms,
        raw=sentry_result.raw,
    )
