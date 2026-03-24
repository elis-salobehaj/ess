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
