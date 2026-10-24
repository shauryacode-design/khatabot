import sqlite3

from cryptography.fernet import Fernet
from sqlalchemy import create_engine

from khata_bot.config import Settings
from khata_bot.services.backup import create_encrypted_backup, restore_encrypted_backup
from khata_bot.services.phones import decrypt_phone, encrypt_phone


def _settings(database_url: str) -> Settings:
    return Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ADMIN_TELEGRAM_ID": "123",
            "GROQ_API_KEY": "test-key",
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "DATABASE_URL": database_url,
        }
    )


def test_encrypted_sqlite_backup_can_be_restored(tmp_path) -> None:
    source = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{source.as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE sample (value TEXT)")
        connection.exec_driver_sql("INSERT INTO sample VALUES ('preserved')")
    engine.dispose()
    settings = _settings(f"sqlite:///{source.as_posix()}")

    backup = create_encrypted_backup(settings, tmp_path / "backups")
    assert not backup.read_bytes().startswith(b"SQLite format 3")
    restored = tmp_path / "restored.db"
    restore_encrypted_backup(settings, backup, restored)

    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("preserved",)


def test_phone_number_is_encrypted_at_rest() -> None:
    key = Fernet.generate_key().decode()
    encrypted = encrypt_phone("+91 98765 43210", key)

    assert "+91 98765 43210" not in encrypted
    assert decrypt_phone(encrypted, key) == "+919876543210"
