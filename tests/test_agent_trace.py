"""Tests for the Phase 1.5 debug trace recorder."""

from __future__ import annotations

import json

import pytest

from src.agent.trace import AgentTraceRecorder


class TestAgentTraceRecorder:
    @pytest.mark.asyncio
    async def test_disabled_recorder_does_not_create_file(self, tmp_path) -> None:
        path = tmp_path / "agent_trace.jsonl"
        recorder = AgentTraceRecorder(enabled=False, path=path)
        session_path = recorder.path_for_trace("ess-1234")
        session_human_path = recorder.human_path_for_trace("ess-1234")

        event = await recorder.emit("cycle.started", trace_id="ess-1234")

        assert event is None
        assert session_path.exists() is False
        assert session_human_path.exists() is False

    @pytest.mark.asyncio
    async def test_enabled_recorder_writes_jsonl_event(self, tmp_path) -> None:
        path = tmp_path / "agent_trace.jsonl"
        recorder = AgentTraceRecorder(enabled=True, path=path)
        session_path = recorder.path_for_trace("ess-1234")
        session_human_path = recorder.human_path_for_trace("ess-1234")

        event = await recorder.emit(
            "cycle.started",
            trace_id="ess-1234",
            cycle_number=1,
            attributes={"services": ["example-service"]},
        )

        assert event is not None
        payload = json.loads(session_path.read_text().strip())
        assert payload["event_type"] == "cycle.started"
        assert payload["trace_id"] == "ess-1234"
        assert payload["cycle_number"] == 1
        assert payload["attributes"]["services"] == ["example-service"]

        digest = session_human_path.read_text()
        assert "# ESS Agent Trace Digest" in digest
        assert "## Session ess-1234" in digest
        assert "### Cycle 1" in digest

    @pytest.mark.asyncio
    async def test_enabled_recorder_writes_human_digest_summary(self, tmp_path) -> None:
        path = tmp_path / "agent_trace.jsonl"
        recorder = AgentTraceRecorder(enabled=True, path=path)
        session_human_path = recorder.human_path_for_trace("ess-1234")

        await recorder.emit(
            "cycle.started",
            trace_id="ess-1234",
            cycle_number=1,
            attributes={
                "services": ["example-service"],
                "environment": "qa",
                "regions": ["qa"],
            },
        )
        await recorder.emit(
            "cycle.completed",
            trace_id="ess-1234",
            cycle_number=1,
            attributes={
                "overall_severity": "HEALTHY",
                "finding_count": 1,
                "findings": [
                    {
                        "severity": "HEALTHY",
                        "tool": "datadog.apm_stats",
                        "summary": "example-service looks healthy",
                    }
                ],
            },
        )
        await recorder.emit(
            "session.completed",
            trace_id="ess-1234",
            cycle_number=1,
            attributes={
                "overall_severity": "HEALTHY",
                "checks_completed": 1,
                "checks_planned": 1,
                "latest_result": {
                    "findings": [
                        {
                            "severity": "HEALTHY",
                            "tool": "datadog.apm_stats",
                            "summary": "example-service looks healthy",
                        }
                    ]
                },
            },
        )

        digest = session_human_path.read_text()
        assert "Cycle completed | severity=HEALTHY | findings=1" in digest
        assert "HEALTHY datadog.apm_stats: example-service looks healthy" in digest
        assert "### Session Summary" in digest

    @pytest.mark.asyncio
    async def test_enabled_recorder_writes_separate_files_per_session(self, tmp_path) -> None:
        path = tmp_path / "agent_trace.jsonl"
        recorder = AgentTraceRecorder(enabled=True, path=path)

        await recorder.emit("cycle.started", trace_id="ess-1111", cycle_number=1)
        await recorder.emit("cycle.started", trace_id="ess-2222", cycle_number=1)

        first_path = recorder.path_for_trace("ess-1111")
        second_path = recorder.path_for_trace("ess-2222")

        assert first_path.name == "agent_trace_ess-1111.jsonl"
        assert second_path.name == "agent_trace_ess-2222.jsonl"
        assert first_path.exists() is True
        assert second_path.exists() is True
