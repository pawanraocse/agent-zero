import pytest

from agent_zero.config import ConfigError, load_config


def test_load_config_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_ZERO_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_ZERO_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_ZERO_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_ZERO_MODEL", raising=False)
    monkeypatch.delenv("AGENT_ZERO_VALIDATION_COMMAND", raising=False)
    monkeypatch.delenv("AGENT_ZERO_VALIDATION_TIMEOUT_SECONDS", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_BASE_URL=https://example.test/v1",
                "AGENT_ZERO_API_KEY=test-key",
                "AGENT_ZERO_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_file)

    assert config.base_url == "https://example.test/v1"
    assert config.api_key == "test-key"
    assert config.model == "test-model"
    assert config.provider == "openai"
    assert config.validation_command is None
    assert config.test_command is None
    assert config.lint_command is None
    assert config.format_command is None
    assert config.validation_timeout_seconds == 120
    assert config.input_cost_per_1m_tokens is None
    assert config.output_cost_per_1m_tokens is None


def test_load_bedrock_config_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_ZERO_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_ZERO_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_ZERO_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_ZERO_MODEL", raising=False)
    monkeypatch.delenv("AGENT_ZERO_BEDROCK_URL", raising=False)
    monkeypatch.delenv("AGENT_ZERO_BEDROCK_AUTH_HEADER", raising=False)
    monkeypatch.delenv("AGENT_ZERO_BEDROCK_TENANT_ID", raising=False)
    monkeypatch.delenv("AGENT_ZERO_VALIDATION_COMMAND", raising=False)
    monkeypatch.delenv("AGENT_ZERO_VALIDATION_TIMEOUT_SECONDS", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_PROVIDER=bedrock",
                "AGENT_ZERO_MODEL=anthropic.test-model",
                "AGENT_ZERO_BEDROCK_URL=https://bedrock.example.test/invoke",
                "AGENT_ZERO_BEDROCK_AUTH_HEADER=x-api-key: test-key",
                "AGENT_ZERO_BEDROCK_TENANT_ID=11221122",
                "AGENT_ZERO_MAX_TOKENS=123",
                "AGENT_ZERO_TOP_P=0.4",
                "AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS=0.5",
                "AGENT_ZERO_BEDROCK_TIMEOUT_SECONDS=30",
                "AGENT_ZERO_VALIDATION_COMMAND=pytest",
                "AGENT_ZERO_TEST_COMMAND=pytest tests",
                "AGENT_ZERO_LINT_COMMAND=ruff check .",
                "AGENT_ZERO_FORMAT_COMMAND=ruff format --check .",
                "AGENT_ZERO_VALIDATION_TIMEOUT_SECONDS=45",
                "AGENT_ZERO_INPUT_COST_PER_1M_TOKENS=1.5",
                "AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS=7.5",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_file)

    assert config.provider == "bedrock"
    assert config.model == "anthropic.test-model"
    assert config.bedrock_url == "https://bedrock.example.test/invoke"
    assert config.bedrock_auth_header == "x-api-key: test-key"
    assert config.bedrock_tenant_id == "11221122"
    assert config.max_tokens == 123
    assert config.top_p == 0.4
    assert config.bedrock_poll_interval_seconds == 0.5
    assert config.bedrock_timeout_seconds == 30
    assert config.validation_command == "pytest"
    assert config.test_command == "pytest tests"
    assert config.lint_command == "ruff check ."
    assert config.format_command == "ruff format --check ."
    assert config.validation_timeout_seconds == 45
    assert config.input_cost_per_1m_tokens == 1.5
    assert config.output_cost_per_1m_tokens == 7.5


def test_load_config_reports_missing_values(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_ZERO_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_ZERO_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_ZERO_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_ZERO_MODEL", raising=False)
    monkeypatch.delenv("AGENT_ZERO_VALIDATION_COMMAND", raising=False)
    monkeypatch.delenv("AGENT_ZERO_VALIDATION_TIMEOUT_SECONDS", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_ZERO_MODEL=test-model\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="AGENT_ZERO_BASE_URL"):
        load_config(env_file)


def test_load_config_reports_invalid_bedrock_header(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_ZERO_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_ZERO_MODEL", raising=False)
    monkeypatch.delenv("AGENT_ZERO_BEDROCK_URL", raising=False)
    monkeypatch.delenv("AGENT_ZERO_BEDROCK_AUTH_HEADER", raising=False)
    monkeypatch.delenv("AGENT_ZERO_BEDROCK_TENANT_ID", raising=False)
    monkeypatch.delenv("AGENT_ZERO_VALIDATION_COMMAND", raising=False)
    monkeypatch.delenv("AGENT_ZERO_VALIDATION_TIMEOUT_SECONDS", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_ZERO_PROVIDER=bedrock",
                "AGENT_ZERO_MODEL=anthropic.test-model",
                "AGENT_ZERO_BEDROCK_URL=https://bedrock.example.test/invoke",
                "AGENT_ZERO_BEDROCK_AUTH_HEADER=test-key",
                "AGENT_ZERO_BEDROCK_TENANT_ID=11221122",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Header-Name: value"):
        load_config(env_file)


def test_load_config_reports_missing_env_file(tmp_path):
    env_file = tmp_path / "missing.env"

    with pytest.raises(ConfigError, match="Environment file does not exist"):
        load_config(env_file)
