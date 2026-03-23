"""ESS LLM client — AWS Bedrock converse API with ABSK bearer-token auth.

The ABSK token is decoded into ``os.environ`` by ``ESSConfig.model_post_init``
before this module is used.  boto3 picks up the credentials automatically via
the standard credential chain.

Two client instances are provided:
- ``triage_client``      → Claude Haiku 4.5 (fast, low-cost health-check triage)
- ``investigation_client`` → Claude Sonnet 4.6 (deeper root-cause reasoning)

Both clients are lazy — the underlying boto3 session is created on first use so
that import-time failures (e.g. missing credentials in tests) are avoided.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config as BotocoreConfig

from src.config import ESSConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Maximum tokens we will allow in a single Bedrock response.
_MAX_TOKENS = 4096

# Bedrock SDK timeout: 30 s connect, 120 s read (streaming responses can be slow).
_BOTO_CONFIG = BotocoreConfig(
    connect_timeout=30,
    read_timeout=120,
    retries={"mode": "standard", "max_attempts": 3},
)


class BedrockClient:
    """Thin async wrapper around boto3's ``bedrock-runtime`` converse API.

    Tool-use (function-calling) is supported via the ``tool_config`` parameter
    on ``converse()``.  The caller is responsible for providing the Bedrock
    tool schema format (``{"tools": [{"toolSpec": {...}}]}``) and for
    interpreting ``toolUse`` blocks in the response.
    """

    def __init__(self, model_id: str, config: ESSConfig) -> None:
        self._model_id = model_id
        self._config = config
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                service_name="bedrock-runtime",
                region_name=self._config.aws_bedrock_region,
                config=_BOTO_CONFIG,
            )
        return self._client

    async def converse(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tool_config: dict[str, Any] | None = None,
        max_tokens: int = _MAX_TOKENS,
    ) -> dict[str, Any]:
        """Call Bedrock converse and return the full API response.

        Runs the blocking boto3 call in the default thread-pool executor so
        the event loop is never blocked.
        """
        client = self._get_client()

        kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if tool_config:
            kwargs["toolConfig"] = tool_config

        def _call() -> dict[str, Any]:
            return client.converse(**kwargs)

        try:
            response: dict[str, Any] = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, _call),
                timeout=130,  # slightly above the boto read_timeout
            )
        except TimeoutError as exc:
            raise TimeoutError(f"Bedrock converse timed out for model {self._model_id}") from exc

        logger.debug(
            "Bedrock converse completed",
            extra={
                "model": self._model_id,
                "input_tokens": response.get("usage", {}).get("inputTokens"),
                "output_tokens": response.get("usage", {}).get("outputTokens"),
                "stop_reason": response.get("stopReason"),
            },
        )
        return response

    @staticmethod
    def extract_text(response: dict[str, Any]) -> str:
        """Extract the first text block from a converse response."""
        content = response.get("output", {}).get("message", {}).get("content", [])
        for block in content:
            if isinstance(block, dict) and "text" in block:
                return block["text"]
        return ""

    @staticmethod
    def extract_tool_uses(response: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract all ``toolUse`` blocks from a converse response."""
        content = response.get("output", {}).get("message", {}).get("content", [])
        return [block["toolUse"] for block in content if "toolUse" in block]


def build_user_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"text": text}]}


def build_tool_result_message(
    tool_use_id: str,
    content: Any,
    is_error: bool = False,
) -> dict[str, Any]:
    """Build a ``tool`` role message (Bedrock converse format)."""
    return {
        "role": "user",
        "content": [
            {
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"text": json.dumps(content)}],
                    **({"status": "error"} if is_error else {}),
                }
            }
        ],
    }


def build_assistant_message(response: dict[str, Any]) -> dict[str, Any]:
    """Re-package the assistant content block for multi-turn continuation."""
    content = response.get("output", {}).get("message", {}).get("content", [])
    return {"role": "assistant", "content": content}


# ---------------------------------------------------------------------------
# Module-level client instances (constructed lazily)
# ---------------------------------------------------------------------------


def make_triage_client(config: ESSConfig) -> BedrockClient:
    return BedrockClient(model_id=config.triage_model, config=config)


def make_investigation_client(config: ESSConfig) -> BedrockClient:
    return BedrockClient(model_id=config.investigation_model, config=config)
