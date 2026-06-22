from types import SimpleNamespace

import pytest
from openai import OpenAIError

from agent_zero.config import AgentConfig
from agent_zero.model_client import (
    BedrockGatewayClient,
    ModelClientError,
    OpenAIModelClient,
    create_model_client,
)


def test_model_client_sends_chat_completion(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Hello from the model.")
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=3,
                    completion_tokens=4,
                    total_tokens=7,
                ),
            )

    class FakeOpenAI:
        def __init__(self, api_key: str, base_url: str, http_client):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            captured["http_client"] = http_client
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("agent_zero.model_client.OpenAI", FakeOpenAI)

    config = AgentConfig(
        AGENT_ZERO_BASE_URL="https://example.test/v1",
        AGENT_ZERO_API_KEY="test-key",
        AGENT_ZERO_MODEL="test-model",
    )

    client = OpenAIModelClient(config)
    response = client.complete("system", "user")

    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://example.test/v1"
    assert captured["http_client"] is not None
    assert captured["model"] == "test-model"
    assert captured["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert response.content == "Hello from the model."
    assert response.total_tokens == 7


def test_model_client_wraps_openai_errors(monkeypatch):
    class FakeCompletions:
        def create(self, **kwargs):
            raise OpenAIError("request failed")

    class FakeOpenAI:
        def __init__(self, api_key: str, base_url: str, http_client):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("agent_zero.model_client.OpenAI", FakeOpenAI)

    config = AgentConfig(
        AGENT_ZERO_BASE_URL="https://example.test/v1",
        AGENT_ZERO_API_KEY="test-key",
        AGENT_ZERO_MODEL="test-model",
    )

    client = OpenAIModelClient(config)

    with pytest.raises(ModelClientError, match="request failed"):
        client.complete("system", "user")


def test_create_model_client_returns_bedrock_client():
    config = AgentConfig(
        AGENT_ZERO_PROVIDER="bedrock",
        AGENT_ZERO_MODEL="anthropic.test-model",
        AGENT_ZERO_BEDROCK_URL="https://bedrock.example.test/invoke",
        AGENT_ZERO_BEDROCK_AUTH_HEADER="x-api-key: test-key",
        AGENT_ZERO_BEDROCK_TENANT_ID="11221122",
    )

    assert isinstance(create_model_client(config), BedrockGatewayClient)


def test_bedrock_gateway_client_submits_and_polls(monkeypatch):
    captured = {}

    class SubmitResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "request-123"}

    class PollResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "completed",
                "completion": "Hello from Bedrock.",
                "usage": {
                    "inputTokens": 5,
                    "outputTokens": 6,
                    "totalTokens": 11,
                },
            }

    class FakeHTTPClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def post(self, url, headers, json):
            captured["post_url"] = url
            captured["post_headers"] = headers
            captured["post_json"] = json
            return SubmitResponse()

        def get(self, url, headers, params):
            captured["get_url"] = url
            captured["get_headers"] = headers
            captured["get_params"] = params
            return PollResponse()

    monkeypatch.setattr("agent_zero.model_client.httpx.Client", FakeHTTPClient)

    config = AgentConfig(
        AGENT_ZERO_PROVIDER="bedrock",
        AGENT_ZERO_MODEL="anthropic.test-model",
        AGENT_ZERO_BEDROCK_URL="https://bedrock.example.test/invoke",
        AGENT_ZERO_BEDROCK_AUTH_HEADER="x-api-key: test-key",
        AGENT_ZERO_BEDROCK_TENANT_ID="11221122",
        AGENT_ZERO_MAX_TOKENS=123,
        AGENT_ZERO_TOP_P=0.4,
        AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS=0,
    )

    client = BedrockGatewayClient(config)
    response = client.complete("system", "user")

    assert captured["client_kwargs"] == {"timeout": 60}
    assert captured["post_url"] == "https://bedrock.example.test/invoke"
    assert captured["post_headers"] == {
        "content-type": "application/json",
        "x-api-key": "test-key",
    }
    assert captured["post_json"] == {
        "prompt": "Human: system\n\nuser\nAssistant:",
        "topP": 0.4,
        "maxTokens": 123,
        "model": "anthropic.test-model",
        "tenantId": "11221122",
    }
    assert captured["get_url"] == "https://bedrock.example.test/invoke/request-123"
    assert captured["get_headers"] == {
        "content-type": "application/json",
        "x-api-key": "test-key",
    }
    assert captured["get_params"] == {"tenantId": "11221122"}
    assert response.content == "Hello from Bedrock."
    assert response.total_tokens == 11


def test_bedrock_gateway_client_waits_for_pending_status(monkeypatch):
    captured = {"sleep_calls": 0}
    poll_responses = [
        {"status": "pending"},
        {"status": "completed", "response": "Done."},
    ]

    class SubmitResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"requestId": "request-123"}}

    class PollResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    class FakeHTTPClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, headers, json):
            return SubmitResponse()

        def get(self, url, headers, params):
            return PollResponse(poll_responses.pop(0))

    monkeypatch.setattr("agent_zero.model_client.httpx.Client", FakeHTTPClient)

    def fake_sleep(seconds):
        captured["sleep_calls"] += 1
        captured["sleep_seconds"] = seconds

    monkeypatch.setattr("agent_zero.model_client.time.sleep", fake_sleep)

    config = AgentConfig(
        AGENT_ZERO_PROVIDER="bedrock",
        AGENT_ZERO_MODEL="anthropic.test-model",
        AGENT_ZERO_BEDROCK_URL="https://bedrock.example.test/invoke",
        AGENT_ZERO_BEDROCK_AUTH_HEADER="x-api-key: test-key",
        AGENT_ZERO_BEDROCK_TENANT_ID="11221122",
        AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS=0.25,
    )

    client = BedrockGatewayClient(config)
    response = client.complete("system", "user")

    assert response.content == "Done."
    assert captured["sleep_calls"] == 1
    assert captured["sleep_seconds"] == 0.25


def test_bedrock_gateway_client_accepts_string_output(monkeypatch):
    class SubmitResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "request-123"}

    class PollResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "completed",
                "output": "String output from the gateway.",
            }

    class FakeHTTPClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, headers, json):
            return SubmitResponse()

        def get(self, url, headers, params):
            return PollResponse()

    monkeypatch.setattr("agent_zero.model_client.httpx.Client", FakeHTTPClient)

    config = AgentConfig(
        AGENT_ZERO_PROVIDER="bedrock",
        AGENT_ZERO_MODEL="anthropic.test-model",
        AGENT_ZERO_BEDROCK_URL="https://bedrock.example.test/invoke",
        AGENT_ZERO_BEDROCK_AUTH_HEADER="x-api-key: test-key",
        AGENT_ZERO_BEDROCK_TENANT_ID="11221122",
        AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS=0,
    )

    client = BedrockGatewayClient(config)
    response = client.complete("system", "user")

    assert response.content == "String output from the gateway."


def test_bedrock_gateway_client_extracts_nested_anthropic_usage(monkeypatch):
    class SubmitResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "request-123"}

    class PollResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "status": "completed",
                    "content": [{"text": "Nested usage response."}],
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 8,
                    },
                }
            }

    class FakeHTTPClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, headers, json):
            return SubmitResponse()

        def get(self, url, headers, params):
            return PollResponse()

    monkeypatch.setattr("agent_zero.model_client.httpx.Client", FakeHTTPClient)

    config = AgentConfig(
        AGENT_ZERO_PROVIDER="bedrock",
        AGENT_ZERO_MODEL="anthropic.test-model",
        AGENT_ZERO_BEDROCK_URL="https://bedrock.example.test/invoke",
        AGENT_ZERO_BEDROCK_AUTH_HEADER="x-api-key: test-key",
        AGENT_ZERO_BEDROCK_TENANT_ID="11221122",
        AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS=0,
    )

    client = BedrockGatewayClient(config)
    response = client.complete("system", "user")

    assert response.content == "Nested usage response."
    assert response.input_tokens == 12
    assert response.output_tokens == 8
    assert response.total_tokens == 20


def test_bedrock_gateway_client_extracts_token_usage_aliases(monkeypatch):
    class SubmitResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "completed",
                "completion": "Immediate response.",
                "tokenUsage": {
                    "prompt_tokens": "20",
                    "completion_tokens": "7",
                    "total_tokens": "27",
                },
            }

    class FakeHTTPClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, headers, json):
            return SubmitResponse()

    monkeypatch.setattr("agent_zero.model_client.httpx.Client", FakeHTTPClient)

    config = AgentConfig(
        AGENT_ZERO_PROVIDER="bedrock",
        AGENT_ZERO_MODEL="anthropic.test-model",
        AGENT_ZERO_BEDROCK_URL="https://bedrock.example.test/invoke",
        AGENT_ZERO_BEDROCK_AUTH_HEADER="x-api-key: test-key",
        AGENT_ZERO_BEDROCK_TENANT_ID="11221122",
        AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS=0,
    )

    client = BedrockGatewayClient(config)
    response = client.complete("system", "user")

    assert response.content == "Immediate response."
    assert response.input_tokens == 20
    assert response.output_tokens == 7
    assert response.total_tokens == 27
