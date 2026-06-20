import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
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
    model: str = Field(alias="AGENT_ZERO_MODEL")
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


def load_config(env_file: Path | None = None) -> AgentConfig:
    """Load Agent Zero config from a .env file and environment variables."""
    if env_file is None:
        load_dotenv()
    else:
        if not env_file.exists():
            raise ConfigError(f"Environment file does not exist: {env_file}")
        load_dotenv(env_file)

    raw_config = {
        "AGENT_ZERO_PROVIDER": os.getenv("AGENT_ZERO_PROVIDER", "openai"),
        "AGENT_ZERO_BASE_URL": os.getenv("AGENT_ZERO_BASE_URL"),
        "AGENT_ZERO_API_KEY": os.getenv("AGENT_ZERO_API_KEY"),
        "AGENT_ZERO_MODEL": os.getenv("AGENT_ZERO_MODEL"),
        "AGENT_ZERO_BEDROCK_URL": os.getenv("AGENT_ZERO_BEDROCK_URL"),
        "AGENT_ZERO_BEDROCK_AUTH_HEADER": os.getenv("AGENT_ZERO_BEDROCK_AUTH_HEADER"),
        "AGENT_ZERO_BEDROCK_TENANT_ID": os.getenv("AGENT_ZERO_BEDROCK_TENANT_ID"),
        "AGENT_ZERO_MAX_TOKENS": os.getenv("AGENT_ZERO_MAX_TOKENS", "4096"),
        "AGENT_ZERO_TOP_P": os.getenv("AGENT_ZERO_TOP_P", "0.2"),
        "AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS": os.getenv(
            "AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS",
            "1.0",
        ),
        "AGENT_ZERO_BEDROCK_TIMEOUT_SECONDS": os.getenv(
            "AGENT_ZERO_BEDROCK_TIMEOUT_SECONDS",
            "120.0",
        ),
    }

    try:
        config = AgentConfig.model_validate(raw_config)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc

    _validate_provider_config(config)
    return config


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
