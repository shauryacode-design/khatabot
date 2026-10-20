from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker

from khata_bot.config import Settings
from khata_bot.database import create_session_factory


def test_database_factory_applies_migrations(tmp_path) -> None:
    database_path = tmp_path / "khata.db"
    settings = Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ADMIN_TELEGRAM_ID": "123456",
            "GROQ_API_KEY": "test-groq-key",
            "ENCRYPTION_KEY": "test-encryption-key",
            "BACKUP_ENCRYPTION_KEY": "test-backup-key",
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
        }
    )

    sessions = create_session_factory(settings)
    engine = sessions.kw["bind"]
    table_names = set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()

    assert isinstance(sessions, sessionmaker)
    assert {"shops", "users", "alembic_version"} <= table_names
    assert journal_mode.lower() == "wal"
