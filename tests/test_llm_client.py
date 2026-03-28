"""Unit tests for BedrockClient helper methods and message builders.

These tests exercise the pure-function parts of llm_client.py without
making any real AWS calls.
"""

from __future__ import annotations

import json
from unittest.mock import sentinel

import pytest

from src.config import ESSConfig
from src.llm_client import (
    BedrockClient,
    build_assistant_message,
    build_tool_result_message,
    build_user_message,
    make_investigation_client,
    make_triage_client,
)


def _response_with_text(text: str) -> dict:
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 10, "outputTokens": 5},
        "stopReason": "end_turn",
    }


def _response_with_tool_use(tool_name: str, tool_id: str, inp: dict) -> dict:
    return {
        "output": {
            "message": {
                "content": [{"toolUse": {"toolUseId": tool_id, "name": tool_name, "input": inp}}]
            }
        },
        "usage": {"inputTokens": 20, "outputTokens": 8},
        "stopReason": "tool_use",
    }


class TestExtractText:
    def test_extracts_first_text_block(self) -> None:
        resp = _response_with_text("hello world")
        assert BedrockClient.extract_text(resp) == "hello world"

    def test_returns_empty_string_when_no_text_block(self) -> None:
        resp = {"output": {"message": {"content": []}}}
        assert BedrockClient.extract_text(resp) == ""

    def test_returns_empty_string_on_empty_response(self) -> None:
        assert BedrockClient.extract_text({}) == ""

    def test_extracts_first_block_when_multiple_present(self) -> None:
        resp = {"output": {"message": {"content": [{"text": "first"}, {"text": "second"}]}}}
        assert BedrockClient.extract_text(resp) == "first"


class TestExtractToolUses:
    def test_extracts_tool_use_block(self) -> None:
        resp = _response_with_tool_use("get_monitors", "tu-001", {"service": "svc-a"})
        uses = BedrockClient.extract_tool_uses(resp)
        assert len(uses) == 1
        assert uses[0]["name"] == "get_monitors"
        assert uses[0]["toolUseId"] == "tu-001"

    def test_returns_empty_list_when_no_tool_use(self) -> None:
        resp = _response_with_text("no tools here")
        assert BedrockClient.extract_tool_uses(resp) == []

    def test_returns_empty_list_on_empty_response(self) -> None:
        assert BedrockClient.extract_tool_uses({}) == []

    def test_extracts_multiple_tool_uses(self) -> None:
        resp = {
            "output": {
                "message": {
                    "content": [
                        {"toolUse": {"toolUseId": "tu-1", "name": "tool_a", "input": {}}},
                        {"toolUse": {"toolUseId": "tu-2", "name": "tool_b", "input": {}}},
                    ]
                }
            }
        }
        uses = BedrockClient.extract_tool_uses(resp)
        assert len(uses) == 2
        assert uses[0]["name"] == "tool_a"
        assert uses[1]["name"] == "tool_b"


class TestBuildUserMessage:
    def test_structure(self) -> None:
        msg = build_user_message("check monitors")
        assert msg["role"] == "user"
        assert msg["content"] == [{"text": "check monitors"}]


class TestBuildToolResultMessage:
    def test_success_result(self) -> None:
        msg = build_tool_result_message("tu-001", {"monitors": []})
        assert msg["role"] == "user"
        content = msg["content"][0]["toolResult"]
        assert content["toolUseId"] == "tu-001"
        assert json.loads(content["content"][0]["text"]) == {"monitors": []}
        assert "status" not in content  # no status field on success

    def test_error_result_includes_status(self) -> None:
        msg = build_tool_result_message("tu-002", {"error": "timeout"}, is_error=True)
        content = msg["content"][0]["toolResult"]
        assert content["status"] == "error"


class TestBuildAssistantMessage:
    def test_preserves_content(self) -> None:
        resp = _response_with_text("I will check monitors now.")
        msg = build_assistant_message(resp)
        assert msg["role"] == "assistant"
        assert msg["content"] == [{"text": "I will check monitors now."}]

    def test_empty_content_on_empty_response(self) -> None:
        msg = build_assistant_message({})
        assert msg["role"] == "assistant"
        assert msg["content"] == []


class TestClientFactory:
    def test_bedrock_client_uses_native_bearer_auth(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def _fake_client(**kwargs):
            captured.update(kwargs)
            return sentinel.client

        monkeypatch.setattr("src.llm_client.boto3.client", _fake_client)

        config = ESSConfig(
            _env_file=None,
            aws_bearer_token_bedrock="ABSKexampletoken",
            aws_bedrock_region="us-west-2",
            dd_api_key="k",
            dd_app_key="a",
            sentry_auth_token="s",
        )
        client = make_triage_client(config)

        assert client._get_client() is sentinel.client
        assert captured["service_name"] == "bedrock-runtime"
        assert captured["region_name"] == "us-west-2"
        assert "aws_access_key_id" not in captured
        assert "aws_secret_access_key" not in captured

    def test_triage_client_uses_correct_model(self, minimal_config) -> None:
        client = make_triage_client(minimal_config)
        assert "sonnet" in client._model_id.lower()

    def test_investigation_client_uses_correct_model(self, minimal_config) -> None:
        client = make_investigation_client(minimal_config)
        assert "sonnet" in client._model_id.lower()
