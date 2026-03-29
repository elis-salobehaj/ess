"""ESS self-observability metrics exposed in Prometheus text format."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock


@dataclass
class _ToolStats:
    calls: int = 0
    duration_ms_total: int = 0


class ESSMetrics:
    """In-memory process metrics for ESS runtime behaviour."""

    def __init__(
        self,
        active_sessions_provider: Callable[[], int] | None = None,
    ) -> None:
        self._active_sessions_provider = active_sessions_provider or (lambda: 0)
        self._checks_executed = 0
        self._alerts_sent = 0
        self._tool_stats: dict[str, _ToolStats] = {}
        self._lock = Lock()

    def set_active_sessions_provider(self, provider: Callable[[], int]) -> None:
        self._active_sessions_provider = provider

    def record_check_executed(self) -> None:
        with self._lock:
            self._checks_executed += 1

    def record_alert_sent(self) -> None:
        with self._lock:
            self._alerts_sent += 1

    def record_tool_call(self, tool: str, duration_ms: int) -> None:
        with self._lock:
            stats = self._tool_stats.setdefault(tool, _ToolStats())
            stats.calls += 1
            stats.duration_ms_total += max(0, duration_ms)

    def render_prometheus(self) -> str:
        with self._lock:
            checks_executed = self._checks_executed
            alerts_sent = self._alerts_sent
            tool_stats = {
                tool: _ToolStats(calls=stats.calls, duration_ms_total=stats.duration_ms_total)
                for tool, stats in self._tool_stats.items()
            }

        lines = [
            "# HELP ess_active_sessions Number of active monitoring sessions.",
            "# TYPE ess_active_sessions gauge",
            f"ess_active_sessions {self._active_sessions_provider()}",
            "# HELP ess_checks_executed_total Number of health-check cycles completed.",
            "# TYPE ess_checks_executed_total counter",
            f"ess_checks_executed_total {checks_executed}",
            "# HELP ess_alerts_sent_total Number of Teams alerts delivered successfully.",
            "# TYPE ess_alerts_sent_total counter",
            f"ess_alerts_sent_total {alerts_sent}",
            "# HELP ess_tool_calls_total Number of external tool calls performed.",
            "# TYPE ess_tool_calls_total counter",
            (
                "# HELP ess_tool_call_duration_ms_total Total duration of external "
                "tool calls in milliseconds."
            ),
            "# TYPE ess_tool_call_duration_ms_total counter",
        ]

        for tool in sorted(tool_stats):
            label = _escape_label_value(tool)
            stats = tool_stats[tool]
            lines.append(f'ess_tool_calls_total{{tool="{label}"}} {stats.calls}')
            lines.append(
                f'ess_tool_call_duration_ms_total{{tool="{label}"}} {stats.duration_ms_total}'
            )

        return "\n".join(lines) + "\n"


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


__all__ = ["ESSMetrics"]
