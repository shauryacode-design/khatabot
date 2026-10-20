"""Database engine and session setup."""

from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
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


def create_session_factory(settings: Settings) -> sessionmaker:
    """Apply database migrations and create this bot's session factory."""
    engine = create_db_engine(settings.database_url)
    migration_config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with engine.begin() as connection:
        migration_config.attributes["connection"] = connection
        command.upgrade(migration_config, "head")
    return sessionmaker(bind=engine, expire_on_commit=False)
