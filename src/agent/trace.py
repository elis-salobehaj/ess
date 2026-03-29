"""Debug-gated agent trace events and JSONL sink.

This module provides the Phase 1.5 instrumentation seam for ESS. Event models
are typed and correlation-friendly so the local JSONL sink can later be
replaced or augmented by an OpenTelemetry exporter without rewriting the agent
loop itself.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class AgentTraceEvent(BaseModel):
    """One observable agent execution event."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str
    parent_event_id: str | None = None
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    cycle_number: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class AgentTraceRecorder:
    """Append per-session JSONL trace events and human-readable digests."""

    def __init__(
        self,
        *,
        enabled: bool,
        path: str | Path,
        human_readable_path: str | Path | None = None,
    ) -> None:
        self._enabled = enabled
        self._path = Path(path)
        self._human_path = (
            Path(human_readable_path)
            if human_readable_path is not None
            else self._derive_human_path(self._path)
        )
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def path(self) -> Path:
        return self._path

    @property
    def human_path(self) -> Path:
        return self._human_path

    def path_for_trace(self, trace_id: str) -> Path:
        return self._suffix_path_with_trace_id(self._path, trace_id)

    def human_path_for_trace(self, trace_id: str) -> Path:
        return self._suffix_path_with_trace_id(self._human_path, trace_id)

    async def emit(
        self,
        event_type: str,
        *,
        trace_id: str,
        cycle_number: int | None = None,
        parent_event_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> AgentTraceEvent | None:
        if not self._enabled:
            return None

        event = AgentTraceEvent(
            trace_id=trace_id,
            parent_event_id=parent_event_id,
            event_type=event_type,
            cycle_number=cycle_number,
            attributes=attributes or {},
        )
        await self.emit_event(event)
        return event

    async def emit_event(self, event: AgentTraceEvent) -> AgentTraceEvent | None:
        if not self._enabled:
            return None

        payload = event.model_dump_json(exclude_none=True)
        human_block = self._render_human_block(event)
        async with self._lock:
            await asyncio.to_thread(
                self._append_outputs,
                event.trace_id,
                payload,
                human_block,
            )
        return event

    def _append_outputs(self, trace_id: str, payload: str, human_block: str | None) -> None:
        session_path = self.path_for_trace(trace_id)
        session_human_path = self.human_path_for_trace(trace_id)

        session_path.parent.mkdir(parents=True, exist_ok=True)
        with session_path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")

        if human_block is None:
            return

        session_human_path.parent.mkdir(parents=True, exist_ok=True)
        digest_exists = session_human_path.exists()
        with session_human_path.open("a", encoding="utf-8") as handle:
            if not digest_exists:
                handle.write("# ESS Agent Trace Digest\n\n")
            handle.write(human_block)
            if not human_block.endswith("\n"):
                handle.write("\n")

    def _derive_human_path(self, path: Path) -> Path:
        return path.with_name(f"{path.stem}_digest.md")

    def _suffix_path_with_trace_id(self, path: Path, trace_id: str) -> Path:
        suffix = "".join(path.suffixes)
        stem = path.name[: -len(suffix)] if suffix else path.name
        safe_trace_id = "".join(
            character if character.isalnum() or character in {"-", "_"} else "_"
            for character in trace_id
        )
        return path.with_name(f"{stem}_{safe_trace_id}{suffix}")

    def _render_human_block(self, event: AgentTraceEvent) -> str | None:
        timestamp = event.timestamp.isoformat()
        attributes = event.attributes

        if event.event_type == "cycle.started":
            services = self._join_values(attributes.get("services"))
            environment = str(attributes.get("environment", "unknown"))
            regions = self._join_values(attributes.get("regions"))
            lines: list[str] = []
            if event.cycle_number == 1:
                lines.append(f"## Session {event.trace_id}")
            lines.append(f"### Cycle {event.cycle_number or '?'}")
            lines.append(
                f"- {timestamp} Started"
                f" | env={environment}"
                f" | regions={regions}"
                f" | services={services}"
            )
            return "\n".join(lines) + "\n\n"

        if event.event_type == "bedrock.request":
            return (
                f"- {timestamp} Bedrock request | iter={attributes.get('iteration', '?')}"
                f" | model={attributes.get('model_id', 'unknown')}\n"
            )

        if event.event_type == "bedrock.response":
            tool_uses = attributes.get("tool_uses")
            tool_use_count = len(tool_uses) if isinstance(tool_uses, list) else 0
            assistant_text = self._first_line(attributes.get("assistant_text"))
            suffix = f" | assistant={assistant_text}" if assistant_text else ""
            return (
                f"- {timestamp} Bedrock response | iter={attributes.get('iteration', '?')}"
                f" | stop={attributes.get('stop_reason', 'unknown')}"
                f" | tool_uses={tool_use_count}{suffix}\n"
            )

        if event.event_type == "agent.error":
            error = attributes.get("error", "unknown error")
            return f"- {timestamp} Agent error | {error}\n"

        if event.event_type == "fallback.triggered":
            return (
                f"- {timestamp} Fallback triggered | reason={attributes.get('reason', 'unknown')}\n"
            )

        if event.event_type == "fallback.started":
            return (
                f"- {timestamp} Fallback started | env={attributes.get('environment', 'unknown')}"
                f" | reason={attributes.get('reason', 'unknown')}\n"
            )

        if event.event_type == "tool.result":
            tool = str(attributes.get("tool", "unknown"))
            status = "ok" if attributes.get("success") else "failed"
            service = attributes.get("service")
            execution_path = attributes.get("execution_path")
            summary = self._first_line(attributes.get("summary") or attributes.get("error"))
            parts = [f"- {timestamp} Tool {tool}", status]
            if service:
                parts.append(f"service={service}")
            if execution_path:
                parts.append(f"path={execution_path}")
            if summary:
                parts.append(summary)
            return " | ".join(parts) + "\n"

        if event.event_type == "cycle.completed":
            lines = [
                f"- {timestamp} Cycle completed"
                f" | severity={attributes.get('overall_severity', 'UNKNOWN')}"
                f" | findings={attributes.get('finding_count', '?')}"
            ]
            findings = attributes.get("findings")
            if isinstance(findings, list):
                for finding in findings[:5]:
                    if not isinstance(finding, dict):
                        continue
                    lines.append(
                        "  - "
                        f"{finding.get('severity', 'UNKNOWN')} {finding.get('tool', 'unknown')}: "
                        f"{finding.get('summary', '')}"
                    )
            return "\n".join(lines) + "\n"

        if event.event_type.startswith("notification."):
            parts = [f"- {timestamp} Notification {event.event_type.split('.', 1)[1]}"]
            kind = attributes.get("kind")
            if kind:
                parts.append(f"kind={kind}")
            reason = attributes.get("reason")
            if reason:
                parts.append(f"reason={reason}")
            status_code = attributes.get("status_code")
            if status_code is not None:
                parts.append(f"status={status_code}")
            error = attributes.get("error")
            if error:
                parts.append(f"error={self._first_line(error)}")
            return " | ".join(parts) + "\n"

        if event.event_type == "session.completed":
            latest_result = attributes.get("latest_result")
            lines = [
                "### Session Summary",
                (
                    f"- {timestamp} Completed"
                    f" | overall={attributes.get('overall_severity', 'UNKNOWN')}"
                    f" | checks={attributes.get('checks_completed', 0)}"
                    f"/{attributes.get('checks_planned', 0)}"
                ),
            ]
            if isinstance(latest_result, dict):
                findings = latest_result.get("findings")
                if isinstance(findings, list):
                    for finding in findings[:5]:
                        if not isinstance(finding, dict):
                            continue
                        severity = finding.get("severity", "UNKNOWN")
                        tool = finding.get("tool", "unknown")
                        summary = finding.get("summary", "")
                        lines.append(f"  - {severity} {tool}: {summary}")
            lines.append("")
            return "\n".join(lines)

        return None

    def _join_values(self, value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value) or "unknown"
        if value is None:
            return "unknown"
        return str(value)

    def _first_line(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip().replace("\n", " ")
        return text[:160]


__all__ = ["AgentTraceEvent", "AgentTraceRecorder"]
