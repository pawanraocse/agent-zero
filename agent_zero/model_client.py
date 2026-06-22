from dataclasses import dataclass
import ssl
import time
from typing import Any, Protocol

import httpx
from openai import OpenAI, OpenAIError

from agent_zero.config import AgentConfig


class ModelClientError(RuntimeError):
    """Raised when the configured model call fails."""


@dataclass(frozen=True)
class ModelResponse:
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ModelClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> ModelResponse:
        """Return a model response for the given prompts."""


class OpenAIModelClient:
    """Small wrapper around an OpenAI-compatible chat completion API."""

    def __init__(self, config: AgentConfig) -> None:
        self._model = config.model
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            http_client=_build_http_client(),
        )

    def complete(self, system_prompt: str, user_prompt: str) -> ModelResponse:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except OpenAIError as exc:
            message = str(exc)
            if exc.__cause__ is not None:
                message = f"{message} Cause: {exc.__cause__}"
            raise ModelClientError(message) from exc

        content = response.choices[0].message.content
        if not content:
            raise ModelClientError("Model returned an empty response.")

        usage = response.usage
        return ModelResponse(
            content=content,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
        )


class BedrockGatewayClient:
    """Client for an internal Bedrock HTTP gateway."""

    def __init__(self, config: AgentConfig) -> None:
        if not config.bedrock_url:
            raise ModelClientError("Bedrock URL is required.")
        if not config.bedrock_auth_header:
            raise ModelClientError("Bedrock auth header is required.")
        if not config.bedrock_tenant_id:
            raise ModelClientError("Bedrock tenant ID is required.")

        self._url = config.bedrock_url
        self._auth_header = _parse_header(config.bedrock_auth_header)
        self._model = config.model
        self._tenant_id = config.bedrock_tenant_id
        self._max_tokens = config.max_tokens
        self._top_p = config.top_p
        self._poll_interval_seconds = config.bedrock_poll_interval_seconds
        self._timeout_seconds = config.bedrock_timeout_seconds
        self._client = httpx.Client(timeout=60)

    def complete(self, system_prompt: str, user_prompt: str) -> ModelResponse:
        payload = {
            "prompt": _format_anthropic_prompt(system_prompt, user_prompt),
            "topP": self._top_p,
            "maxTokens": self._max_tokens,
            "model": self._model,
            "tenantId": self._tenant_id,
        }
        headers = {
            "content-type": "application/json",
            self._auth_header[0]: self._auth_header[1],
        }

        try:
            response = self._client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _gateway_error("submit", exc) from exc

        request_id = _extract_request_id(data)
        if not request_id:
            content = _extract_text(data)
            if content:
                return _model_response_from_data(data, content)
            raise ModelClientError("Bedrock gateway did not return a request id.")

        return self._poll_until_complete(request_id, headers)

    def _poll_until_complete(
        self,
        request_id: str,
        headers: dict[str, str],
    ) -> ModelResponse:
        deadline = time.monotonic() + self._timeout_seconds
        poll_url = f"{self._url.rstrip('/')}/{request_id}"

        while time.monotonic() < deadline:
            try:
                response = self._client.get(
                    poll_url,
                    headers=headers,
                    params={"tenantId": self._tenant_id},
                )
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise _gateway_error("poll", exc) from exc

            status = _extract_status(data)
            if status in {"failed", "failure", "error", "errored", "cancelled"}:
                raise ModelClientError(
                    f"Bedrock gateway request {request_id} failed: {data}"
                )

            content = _extract_text(data)
            if content and status not in {"pending", "running", "in_progress"}:
                return _model_response_from_data(data, content)

            time.sleep(self._poll_interval_seconds)

        raise ModelClientError(
            f"Bedrock gateway request {request_id} timed out after "
            f"{self._timeout_seconds} seconds."
        )


def create_model_client(config: AgentConfig) -> ModelClient:
    if config.provider == "bedrock":
        return BedrockGatewayClient(config)
    return OpenAIModelClient(config)


def _build_http_client() -> httpx.Client:
    try:
        import truststore
    except ImportError:
        return httpx.Client()

    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.Client(verify=ssl_context)


def _parse_header(raw_header: str) -> tuple[str, str]:
    name, value = raw_header.split(":", 1)
    return name.strip(), value.strip()


def _format_anthropic_prompt(system_prompt: str, user_prompt: str) -> str:
    return f"Human: {system_prompt}\n\n{user_prompt}\nAssistant:"


def _gateway_error(action: str, exc: Exception) -> ModelClientError:
    if isinstance(exc, httpx.HTTPStatusError):
        return ModelClientError(
            f"Bedrock gateway {action} returned HTTP "
            f"{exc.response.status_code}: {exc.response.text}"
        )
    if isinstance(exc, httpx.HTTPError):
        return ModelClientError(f"Bedrock gateway {action} request failed: {exc}")
    return ModelClientError(f"Bedrock gateway {action} returned invalid JSON.")


def _extract_request_id(data: dict[str, Any]) -> str | None:
    for key in ("id", "requestId", "request_id", "jobId", "job_id", "executionId"):
        value = data.get(key)
        if isinstance(value, str):
            return value

    nested_data = data.get("data")
    if isinstance(nested_data, dict):
        return _extract_request_id(nested_data)

    return None


def _extract_status(data: dict[str, Any]) -> str | None:
    for key in ("status", "state", "requestStatus", "jobStatus"):
        value = data.get(key)
        if isinstance(value, str):
            return value.lower()

    nested_data = data.get("data")
    if isinstance(nested_data, dict):
        return _extract_status(nested_data)

    return None


def _model_response_from_data(data: dict[str, Any], content: str) -> ModelResponse:
    input_tokens, output_tokens, total_tokens = _extract_usage(data)
    return ModelResponse(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _extract_usage(data: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = _find_usage_data(data)
    if usage is None:
        return None, None, None

    input_tokens = _first_int(
        usage,
        (
            "inputTokens",
            "input_tokens",
            "promptTokens",
            "prompt_tokens",
            "inputTokenCount",
            "input_token_count",
            "promptTokenCount",
            "prompt_token_count",
        ),
    )
    output_tokens = _first_int(
        usage,
        (
            "outputTokens",
            "output_tokens",
            "completionTokens",
            "completion_tokens",
            "generatedTokens",
            "generated_tokens",
            "outputTokenCount",
            "output_token_count",
            "completionTokenCount",
            "completion_token_count",
        ),
    )
    total_tokens = _first_int(
        usage,
        (
            "totalTokens",
            "total_tokens",
            "totalTokenCount",
            "total_token_count",
        ),
    )

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if input_tokens is None and total_tokens is not None and output_tokens is not None:
        input_tokens = total_tokens - output_tokens
    if output_tokens is None and total_tokens is not None and input_tokens is not None:
        output_tokens = total_tokens - input_tokens

    return input_tokens, output_tokens, total_tokens


def _find_usage_data(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        for item in value:
            usage = _find_usage_data(item)
            if usage is not None:
                return usage
        return None

    if not isinstance(value, dict):
        return None

    if _has_token_usage_keys(value):
        return value

    for key in ("usage", "tokenUsage", "token_usage", "metrics", "metadata", "data"):
        usage = _find_usage_data(value.get(key))
        if usage is not None:
            return usage

    for nested_value in value.values():
        usage = _find_usage_data(nested_value)
        if usage is not None:
            return usage

    return None


def _has_token_usage_keys(value: dict[str, Any]) -> bool:
    return any(
        key.lower().replace("_", "")
        in {
            "inputtokens",
            "prompttokens",
            "inputtokencount",
            "prompttokencount",
            "outputtokens",
            "completiontokens",
            "generatedtokens",
            "outputtokencount",
            "completiontokencount",
            "totaltokens",
            "totaltokencount",
        }
        for key in value
    )


def _first_int(value: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        coerced = _coerce_int(value.get(key))
        if coerced is not None:
            return coerced
    return None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _extract_text(data: dict[str, Any]) -> str | None:
    return _extract_text_value(data)


def _extract_text_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        for item in value:
            text = _extract_text_value(item)
            if text:
                return text
        return None

    if not isinstance(value, dict):
        return None

    for key in ("completion", "content", "response", "output", "text", "answer"):
        text = _extract_text_value(value.get(key))
        if text:
            return text

    choices = value.get("choices")
    if isinstance(choices, list) and choices:
        text = _extract_text_value(choices[0].get("message"))
        if text:
            return text

    message = value.get("message")
    text = _extract_text_value(message)
    if text:
        return text

    nested_data = value.get("data")
    text = _extract_text_value(nested_data)
    if text:
        return text

    return None
