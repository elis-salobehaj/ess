"""Tool adapters — Pup CLI (Datadog), Sentry REST, Log Scout HTTP.

Implemented adapters:
- ``pup_tool.PupTool``      — Datadog via Pup CLI subprocess (Phase D1)
- ``normalise.pup_to_tool_result`` — converts PupResult → ToolResult (Phase D1)
- ``sentry_tool.SentryTool`` — Sentry via REST API (Phase S1/S2)
- ``normalise.sentry_*_to_tool_result`` — converts SentryResult → ToolResult

Planned:
- Log Scout HTTP adapter (Phase D3 of Log Scout deliverable)
"""
