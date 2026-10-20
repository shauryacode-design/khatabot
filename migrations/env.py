"""Alembic environment for the Khata Bot database."""

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

from khata_bot.config import load_settings
from khata_bot.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    settings = load_settings()
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        settings = load_settings()
        engine = create_engine(
            settings.database_url, poolclass=pool.NullPool
        )
        with engine.connect() as connection:
            _run_with_connection(connection)
        return

    _run_with_connection(connection)


def _run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
