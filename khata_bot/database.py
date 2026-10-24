"""Database engine and session setup."""

from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import sessionmaker

from khata_bot.config import Settings


def create_db_engine(database_url: str) -> Engine:
    """Create an engine and enable SQLite's WAL and foreign-key support."""
    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite:"):
        engine_options["connect_args"] = {"check_same_thread": False}

    engine = create_engine(database_url, **engine_options)

    if database_url.startswith("sqlite:"):

        @event.listens_for(engine, "connect")
        def set_sqlite_options(connection: sqlite3.Connection, _record: object) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def _is_compatible_unversioned_schema(connection: Connection) -> bool:
    """Recognize tables created by the original Phase 1 create_all startup."""
    inspector = inspect(connection)
    if not {"shops", "users"}.issubset(inspector.get_table_names()):
        return False

    expected_columns = {
        "shops": {"id", "name", "created_at"},
        "users": {"id", "telegram_user_id", "shop_id", "role", "created_at"},
    }
    for table_name, columns in expected_columns.items():
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        if actual_columns != columns:
            return False
        if inspector.get_pk_constraint(table_name).get("constrained_columns") != ["id"]:
            return False

    unique_user_ids = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("users")
    }
    foreign_keys = inspector.get_foreign_keys("users")
    if ("telegram_user_id",) not in unique_user_ids:
        return False
    if not any(
        key["constrained_columns"] == ["shop_id"]
        and key["referred_table"] == "shops"
        and key["referred_columns"] == ["id"]
        for key in foreign_keys
    ):
        return False

    return True


def create_session_factory(settings: Settings) -> sessionmaker:
    """Apply database migrations and create this bot's session factory."""
    engine = create_db_engine(settings.database_url)
    migration_config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with engine.begin() as connection:
        migration_config.attributes["connection"] = connection
        current_heads = MigrationContext.configure(connection).get_current_heads()
        if not current_heads and _is_compatible_unversioned_schema(connection):
            # Earlier Phase 1 builds created these exact tables with create_all.
            # Mark them as the initial revision instead of trying to recreate them.
            command.stamp(migration_config, "0001_initial")
        elif not current_heads and {"shops", "users"}.intersection(
            inspect(connection).get_table_names()
        ):
            raise RuntimeError(
                "The existing database has an unversioned or incomplete Khata "
                "schema. Back it up and inspect it before applying migrations."
            )
        command.upgrade(migration_config, "head")
    return sessionmaker(bind=engine, expire_on_commit=False)
