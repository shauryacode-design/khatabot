from datetime import date

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from cryptography.fernet import Fernet

from khata_bot.config import Settings
from khata_bot.database import create_session_factory
from khata_bot.models import Base, Customer, Entry, Shop, User


def test_database_factory_applies_migrations(tmp_path) -> None:
    database_path = tmp_path / "khata%db.db"
    settings = Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ADMIN_TELEGRAM_ID": "123456",
            "GROQ_API_KEY": "test-groq-key",
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
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
    for table_name in ("shops", "users"):
        Base.metadata.tables[table_name].create(old_engine)
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
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "DATABASE_URL": database_url,
        }
    )

    sessions = create_session_factory(settings)
    with sessions() as session:
        assert session.query(Shop).one().name == "Existing Shop"
        assert session.query(User).one().telegram_user_id == 123456
    with sessions.kw["bind"].connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version"))
        assert version.scalar_one() == "9f3c2d1a7b64"


def test_telegram_id_columns_migrate_up_and_down(tmp_path) -> None:
    database_path = tmp_path / "telegram-ids.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    settings = Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ADMIN_TELEGRAM_ID": "123456",
            "GROQ_API_KEY": "test-groq-key",
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "DATABASE_URL": database_url,
        }
    )
    sessions = create_session_factory(settings)
    engine = sessions.kw["bind"]
    migration_config = Config("alembic.ini")
    columns = (
        ("entries", "created_by"),
        ("payments", "created_by"),
        ("audit_log", "user_id"),
        ("invites", "created_by"),
    )

    def assert_column_types(expected: str) -> None:
        inspector = inspect(engine)
        for table_name, column_name in columns:
            column = next(
                item
                for item in inspector.get_columns(table_name)
                if item["name"] == column_name
            )
            assert column["type"].compile(engine.dialect).upper() == expected

    assert_column_types("BIGINT")
    with engine.begin() as connection:
        migration_config.attributes["connection"] = connection
        command.downgrade(migration_config, "6d62fb347d59")
    assert_column_types("INTEGER")
    with engine.begin() as connection:
        migration_config.attributes["connection"] = connection
        command.upgrade(migration_config, "head")
    assert_column_types("BIGINT")
    engine.dispose()


def test_telegram_id_migration_refuses_unsafe_downgrade(tmp_path) -> None:
    database_path = tmp_path / "telegram-ids-range.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    settings = Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ADMIN_TELEGRAM_ID": "123456",
            "GROQ_API_KEY": "test-groq-key",
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "DATABASE_URL": database_url,
        }
    )
    sessions = create_session_factory(settings)
    engine = sessions.kw["bind"]
    with sessions.begin() as session:
        shop = Shop(name="Range check")
        session.add(shop)
        session.flush()
        customer = Customer(
            shop_id=shop.id, name="Aman", normalized_name="aman"
        )
        session.add(customer)
        session.flush()
        session.add(
            Entry(
                shop_id=shop.id,
                customer_id=customer.id,
                entry_date=date(2026, 10, 24),
                item_name="aata",
                price_paise=40000,
                created_by=5_726_633_609,
            )
        )

    migration_config = Config("alembic.ini")
    with pytest.raises(RuntimeError, match="32-bit INTEGER range"):
        with engine.begin() as connection:
            migration_config.attributes["connection"] = connection
            command.downgrade(migration_config, "6d62fb347d59")
    with engine.connect() as connection:
        version = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert version == "9f3c2d1a7b64"
    engine.dispose()
