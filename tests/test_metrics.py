"""Unit tests for the ESS self-observability metrics registry."""

from __future__ import annotations

from src.metrics import ESSMetrics


class TestESSMetrics:
    def test_render_prometheus_includes_runtime_counters(self) -> None:
        metrics = ESSMetrics(active_sessions_provider=lambda: 3)

        metrics.record_check_executed()
        metrics.record_check_executed()
        metrics.record_alert_sent()
        metrics.record_tool_call("datadog.pup", 42)
        metrics.record_tool_call("sentry.api", 18)

        rendered = metrics.render_prometheus()

        assert "ess_active_sessions 3" in rendered
        assert "ess_checks_executed_total 2" in rendered
        assert "ess_alerts_sent_total 1" in rendered
        assert 'ess_tool_calls_total{tool="datadog.pup"} 1' in rendered
        assert 'ess_tool_call_duration_ms_total{tool="datadog.pup"} 42' in rendered
        assert 'ess_tool_calls_total{tool="sentry.api"} 1' in rendered
        assert 'ess_tool_call_duration_ms_total{tool="sentry.api"} 18' in rendered
