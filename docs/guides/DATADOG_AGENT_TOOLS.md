# Datadog Agent Tools

This guide covers the Datadog Bedrock tool-definition layer added for Deliverable 1 Phase D3.

The implementation lives in `src/agent/datadog_tools.py` and provides three things:

1. Bedrock-compatible Datadog tool schemas via `DATADOG_TOOL_CONFIG`
2. Datadog-specific system-prompt text via `build_datadog_tool_prompt_fragment()`
3. Validated dispatch from Bedrock `toolUse` blocks to `PupTool` methods

At runtime, these definitions are now consumed by `src/agent/health_check_agent.py`, which runs the first Datadog-only Bedrock tool loop for each monitoring cycle and falls back to deterministic Pup triage if the LLM path fails.

## Supported Datadog tools

- `datadog_monitor_status`
- `datadog_error_logs`
- `datadog_apm_stats`
- `datadog_incidents`
- `datadog_infrastructure_health`
- `datadog_apm_operations`

These map to the existing `PupTool` triage and investigation helpers. Tool inputs are validated with Pydantic before a Pup subprocess is executed.

The repository also includes a realistic trigger example at `docs/examples/triggers/pason-well-service-qa-e2e.json`. The D3 test suite uses that payload to verify the Datadog prompt fragment and mocked Bedrock tool loop against a real deploy shape instead of only synthetic strings.

## Typical usage

```python
from src.agent.datadog_tools import (
    DATADOG_TOOL_CONFIG,
    build_datadog_tool_prompt_fragment,
    execute_datadog_tool_uses,
)
from src.llm_client import BedrockClient, build_assistant_message, build_user_message

messages = [build_user_message("Check whether the deployment is healthy.")]
response = await bedrock_client.converse(
    messages=messages,
    system=build_datadog_tool_prompt_fragment(trigger.services),
    tool_config=DATADOG_TOOL_CONFIG,
)

tool_uses = BedrockClient.extract_tool_uses(response)
results, tool_messages = await execute_datadog_tool_uses(pup_tool, tool_uses)

messages.append(build_assistant_message(response))
messages.extend(tool_messages)
```

## Design notes

- The Bedrock-facing tool names use underscore-separated names such as `datadog_monitor_status`.
- Normalised ESS tool results continue to use dot-namespaced identifiers such as `datadog.monitor_status`.
- Invalid model-supplied inputs return an error `ToolResult` and an error-status Bedrock `toolResult` message.
- The prompt fragment explicitly tells the model to use `datadog_service_name`, not the log service name.
- The tool layer is observation-only. It never performs remediation actions.

## Tests

`tests/test_datadog_tools.py` covers:

- tool schema shape
- prompt fragment rendering
- input validation
- dispatch to the correct `PupTool` method
- round-trip handling of mocked Bedrock `toolUse` blocks