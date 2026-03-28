"""AI orchestrator helpers.

Implemented modules:
- ``datadog_tools``: Bedrock tool schemas, prompt fragments, and Pup dispatch.
- ``health_check_agent``: Datadog-backed Bedrock tool loop with deterministic fallback.
- ``trace``: Debug-gated JSONL trace sink and typed observable event models.

The full ReAct health-check loop lands in Phase 3 of the master plan.
"""
