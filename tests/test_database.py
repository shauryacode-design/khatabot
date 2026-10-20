from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from khata_bot.config import Settings
from khata_bot.database import create_session_factory
from khata_bot.models import Base, Shop, User


def test_database_factory_applies_migrations(tmp_path) -> None:
    database_path = tmp_path / "khata%db.db"
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


def test_database_factory_adopts_existing_phase_one_tables(tmp_path) -> None:
    database_path = tmp_path / "existing.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    old_engine = create_engine(database_url)
    Base.metadata.create_all(old_engine)
    with Session(old_engine) as session:
        shop = Shop(name="Existing Shop")
        session.add(shop)
        session.flush()
        session.add(User(telegram_user_id=123456, shop_id=shop.id))
        session.commit()
    with old_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
    old_engine.dispose()

    settings = Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ADMIN_TELEGRAM_ID": "123456",
            "GROQ_API_KEY": "test-groq-key",
            "ENCRYPTION_KEY": "test-encryption-key",
            "BACKUP_ENCRYPTION_KEY": "test-backup-key",
            "DATABASE_URL": database_url,
        }
    )

    sessions = create_session_factory(settings)
    with sessions() as session:
        assert session.query(Shop).one().name == "Existing Shop"
        assert session.query(User).one().telegram_user_id == 123456
    with sessions.kw["bind"].connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version"))
        assert version.scalar_one() == "0001_initial"
