from dataclasses import dataclass

import tiktoken

from agent_zero.config import AgentConfig
from agent_zero.model_client import ModelResponse


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated: bool


@dataclass(frozen=True)
class UsageCost:
    input_cost: float
    output_cost: float

    @property
    def total_cost(self) -> float:
        return self.input_cost + self.output_cost


def resolve_token_usage(
    response: ModelResponse,
    system_prompt: str,
    user_prompt: str,
    model: str | None,
) -> TokenUsage:
    if response.input_tokens is not None and response.output_tokens is not None:
        total_tokens = response.total_tokens
        if total_tokens is None:
            total_tokens = response.input_tokens + response.output_tokens
        return TokenUsage(
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            total_tokens=total_tokens,
            estimated=False,
        )

    input_tokens = _count_tokens(f"{system_prompt}\n\n{user_prompt}", model)
    output_tokens = _count_tokens(response.content, model)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated=True,
    )


def estimate_usage_cost(usage: TokenUsage, config: AgentConfig) -> UsageCost | None:
    if (
        config.input_cost_per_1m_tokens is None
        or config.output_cost_per_1m_tokens is None
    ):
        return None

    return UsageCost(
        input_cost=(usage.input_tokens / 1_000_000) * config.input_cost_per_1m_tokens,
        output_cost=(usage.output_tokens / 1_000_000)
        * config.output_cost_per_1m_tokens,
    )


def format_usage_cost(cost: UsageCost) -> str:
    return f"${cost.total_cost:.6f}"


def _count_tokens(text: str, model: str | None) -> int:
    try:
        if model is None:
            raise KeyError
        encoding = tiktoken.encoding_for_model(model)
        return len(encoding.encode(text))
    except Exception:
        return _estimate_tokens_without_encoding(text)


def _estimate_tokens_without_encoding(text: str) -> int:
    if not text:
        return 0

    return max(1, round(len(text) / 4))
