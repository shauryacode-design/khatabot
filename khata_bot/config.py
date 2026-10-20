"""Environment-based application configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError


class ConfigurationError(ValueError):
    """Raised when required application settings are missing or invalid."""


class Settings(BaseModel):
    telegram_bot_token: str = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    admin_telegram_id: int = Field(validation_alias="ADMIN_TELEGRAM_ID")
    groq_api_key: str = Field(validation_alias="GROQ_API_KEY")
    groq_model: str = Field(
        default="openai/gpt-oss-120b", validation_alias="GROQ_MODEL"
    )
    database_url: str = Field(
        default="sqlite:///khata.db", validation_alias="DATABASE_URL"
    )
    encryption_key: str = Field(validation_alias="ENCRYPTION_KEY")
    backup_encryption_key: str = Field(validation_alias="BACKUP_ENCRYPTION_KEY")
    max_price_warning: int = Field(
        default=50_000, gt=0, validation_alias="MAX_PRICE_WARNING"
    )
    rate_limit_per_minute: int = Field(
        default=20, gt=0, validation_alias="RATE_LIMIT_PER_MINUTE"
    )


_REQUIRED_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "ADMIN_TELEGRAM_ID",
    "GROQ_API_KEY",
    "ENCRYPTION_KEY",
    "BACKUP_ENCRYPTION_KEY",
)


def load_settings(env_file: str | Path = ".env") -> Settings:
    """Load .env first (overriding stale process variables), then validate settings."""
    load_dotenv(dotenv_path=env_file, override=True)

    missing = [name for name in _REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        raise ConfigurationError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )

    try:
        return Settings.model_validate(os.environ)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
            for issue in error.errors()
        )
        raise ConfigurationError(f"Invalid application configuration: {details}") from error


def get_groq_model() -> str:
    """Read the Groq model at call time so a changed environment value takes effect."""
    return os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
