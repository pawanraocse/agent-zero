import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigError(RuntimeError):
    """Raised when Agent Zero cannot load required configuration."""


class AgentConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: Literal["openai", "bedrock"] = Field(
        default="openai",
        alias="AGENT_ZERO_PROVIDER",
    )
    base_url: str | None = Field(default=None, alias="AGENT_ZERO_BASE_URL")
    api_key: str | None = Field(default=None, alias="AGENT_ZERO_API_KEY")
    model: str | None = Field(default=None, alias="AGENT_ZERO_MODEL")
    bedrock_url: str | None = Field(default=None, alias="AGENT_ZERO_BEDROCK_URL")
    bedrock_auth_header: str | None = Field(
        default=None,
        alias="AGENT_ZERO_BEDROCK_AUTH_HEADER",
    )
    bedrock_tenant_id: str | None = Field(
        default=None,
        alias="AGENT_ZERO_BEDROCK_TENANT_ID",
    )
    max_tokens: int = Field(default=4096, alias="AGENT_ZERO_MAX_TOKENS")
    top_p: float = Field(default=0.2, alias="AGENT_ZERO_TOP_P")
    bedrock_poll_interval_seconds: float = Field(
        default=1.0,
        alias="AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS",
    )
    bedrock_timeout_seconds: float = Field(
        default=120.0,
        alias="AGENT_ZERO_BEDROCK_TIMEOUT_SECONDS",
    )
    validation_command: str | None = Field(
        default=None,
        alias="AGENT_ZERO_VALIDATION_COMMAND",
    )
    validation_timeout_seconds: float = Field(
        default=120.0,
        alias="AGENT_ZERO_VALIDATION_TIMEOUT_SECONDS",
    )
    input_cost_per_1m_tokens: float | None = Field(
        default=None,
        alias="AGENT_ZERO_INPUT_COST_PER_1M_TOKENS",
    )
    output_cost_per_1m_tokens: float | None = Field(
        default=None,
        alias="AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS",
    )


def load_config(env_file: Path | None = None) -> AgentConfig:
    """Load Agent Zero config from a .env file and environment variables."""
    env_values = _load_env_values(env_file)

    raw_config = {
        "AGENT_ZERO_PROVIDER": env_values.get("AGENT_ZERO_PROVIDER", "openai"),
        "AGENT_ZERO_BASE_URL": env_values.get("AGENT_ZERO_BASE_URL"),
        "AGENT_ZERO_API_KEY": env_values.get("AGENT_ZERO_API_KEY"),
        "AGENT_ZERO_MODEL": env_values.get("AGENT_ZERO_MODEL"),
        "AGENT_ZERO_BEDROCK_URL": env_values.get("AGENT_ZERO_BEDROCK_URL"),
        "AGENT_ZERO_BEDROCK_AUTH_HEADER": env_values.get(
            "AGENT_ZERO_BEDROCK_AUTH_HEADER"
        ),
        "AGENT_ZERO_BEDROCK_TENANT_ID": env_values.get("AGENT_ZERO_BEDROCK_TENANT_ID"),
        "AGENT_ZERO_MAX_TOKENS": env_values.get("AGENT_ZERO_MAX_TOKENS", "4096"),
        "AGENT_ZERO_TOP_P": env_values.get("AGENT_ZERO_TOP_P", "0.2"),
        "AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS": env_values.get(
            "AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS",
            "1.0",
        ),
        "AGENT_ZERO_BEDROCK_TIMEOUT_SECONDS": env_values.get(
            "AGENT_ZERO_BEDROCK_TIMEOUT_SECONDS",
            "120.0",
        ),
        "AGENT_ZERO_VALIDATION_COMMAND": env_values.get(
            "AGENT_ZERO_VALIDATION_COMMAND"
        ),
        "AGENT_ZERO_VALIDATION_TIMEOUT_SECONDS": env_values.get(
            "AGENT_ZERO_VALIDATION_TIMEOUT_SECONDS",
            "120.0",
        ),
        "AGENT_ZERO_INPUT_COST_PER_1M_TOKENS": env_values.get(
            "AGENT_ZERO_INPUT_COST_PER_1M_TOKENS"
        ),
        "AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS": env_values.get(
            "AGENT_ZERO_OUTPUT_COST_PER_1M_TOKENS"
        ),
    }

    try:
        config = AgentConfig.model_validate(raw_config)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc

    _validate_provider_config(config)
    return config


def _load_env_values(env_file: Path | None) -> dict[str, str | None]:
    if env_file is not None:
        if not env_file.exists():
            raise ConfigError(f"Environment file does not exist: {env_file}")
        return dict(dotenv_values(env_file))

    values = dict(dotenv_values())
    for key, value in os.environ.items():
        if key.startswith("AGENT_ZERO_"):
            values[key] = value
    return values


def _validate_provider_config(config: AgentConfig) -> None:
    missing = []

    if not config.model:
        missing.append("AGENT_ZERO_MODEL")

    if config.provider == "openai":
        if not config.base_url:
            missing.append("AGENT_ZERO_BASE_URL")
        if not config.api_key:
            missing.append("AGENT_ZERO_API_KEY")

    if config.provider == "bedrock":
        if not config.bedrock_url:
            missing.append("AGENT_ZERO_BEDROCK_URL")
        if not config.bedrock_auth_header:
            missing.append("AGENT_ZERO_BEDROCK_AUTH_HEADER")
        if not config.bedrock_tenant_id:
            missing.append("AGENT_ZERO_BEDROCK_TENANT_ID")
        if config.bedrock_auth_header and ":" not in config.bedrock_auth_header:
            raise ConfigError(
                "AGENT_ZERO_BEDROCK_AUTH_HEADER must use 'Header-Name: value' format"
            )

    if missing:
        names = ", ".join(missing)
        raise ConfigError(f"Missing required configuration: {names}")
