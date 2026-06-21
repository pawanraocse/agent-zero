from agent_zero.config import AgentConfig
from agent_zero.model_client import ModelResponse
from agent_zero.usage import (
    TokenUsage,
    estimate_usage_cost,
    format_usage_cost,
    resolve_token_usage,
)


def test_estimate_usage_cost_when_prices_are_configured():
    config = AgentConfig(
        AGENT_ZERO_BASE_URL="https://example.test/v1",
        AGENT_ZERO_API_KEY="test-key",
        AGENT_ZERO_MODEL="test-model",
        AGENT_ZERO_INPUT_COST_PER_1M_TOKENS=1.0,
        AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS=2.0,
    )
    usage = TokenUsage(
        input_tokens=1000,
        output_tokens=2000,
        total_tokens=3000,
        estimated=False,
    )

    cost = estimate_usage_cost(usage, config)

    assert cost is not None
    assert cost.input_cost == 0.001
    assert cost.output_cost == 0.004
    assert format_usage_cost(cost) == "$0.005000"


def test_estimate_usage_cost_requires_prices_and_token_counts():
    config = AgentConfig(
        AGENT_ZERO_BASE_URL="https://example.test/v1",
        AGENT_ZERO_API_KEY="test-key",
        AGENT_ZERO_MODEL="test-model",
    )
    usage = TokenUsage(
        input_tokens=1000,
        output_tokens=2000,
        total_tokens=3000,
        estimated=False,
    )

    assert estimate_usage_cost(usage, config) is None


def test_resolve_token_usage_uses_provider_counts_when_available():
    response = ModelResponse(
        content="ok",
        input_tokens=1000,
        output_tokens=2000,
    )

    usage = resolve_token_usage(
        response=response,
        system_prompt="system",
        user_prompt="user",
        model="unknown-model",
    )

    assert usage.input_tokens == 1000
    assert usage.output_tokens == 2000
    assert usage.total_tokens == 3000
    assert usage.estimated is False


def test_resolve_token_usage_estimates_counts_when_provider_counts_are_missing():
    response = ModelResponse(content="hello")

    usage = resolve_token_usage(
        response=response,
        system_prompt="system",
        user_prompt="user",
        model="unknown-model",
    )

    assert usage.input_tokens > 0
    assert usage.output_tokens > 0
    assert usage.total_tokens == usage.input_tokens + usage.output_tokens
    assert usage.estimated is True
