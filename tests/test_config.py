import pytest
from cryptography.fernet import Fernet

from khata_bot.config import ConfigurationError, load_settings


def _set_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "123456")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_load_settings_uses_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _set_required_environment(monkeypatch)

    settings = load_settings(tmp_path / "missing.env")

    assert settings.database_url == "sqlite:///khata.db"
    assert settings.groq_model == "openai/gpt-oss-120b"
    assert settings.max_price_warning == 50_000
    assert settings.rate_limit_per_minute == 20


def test_dotenv_overrides_existing_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv("GROQ_MODEL", "stale-model")
    encryption_key = Fernet.generate_key().decode()
    backup_encryption_key = Fernet.generate_key().decode()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=dotenv-token\n"
        "ADMIN_TELEGRAM_ID=654321\n"
        "GROQ_API_KEY=dotenv-groq-key\n"
        f"ENCRYPTION_KEY={encryption_key}\n"
        f"BACKUP_ENCRYPTION_KEY={backup_encryption_key}\n"
        "GROQ_MODEL=dotenv-model\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file)

    assert settings.telegram_bot_token == "dotenv-token"
    assert settings.admin_telegram_id == 654321
    assert settings.groq_model == "dotenv-model"


def test_missing_required_environment_is_clear(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "ADMIN_TELEGRAM_ID",
        "GROQ_API_KEY",
        "ENCRYPTION_KEY",
        "BACKUP_ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        load_settings(tmp_path / "missing.env")


def test_positive_limits_are_validated(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")

    with pytest.raises(ConfigurationError, match="RATE_LIMIT_PER_MINUTE"):
        load_settings(tmp_path / "missing.env")
