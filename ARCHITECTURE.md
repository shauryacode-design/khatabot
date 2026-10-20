# Architecture

This document describes the modules implemented so far. The bot is being built
in phases, so future modules will be added here as their behavior is implemented.

## Message flow so far

1. `khata_bot.bot` loads settings and creates the database session factory.
2. It builds the Telegram application with long-polling timeouts and registers
   command and error handlers.
3. When `/start` arrives, `khata_bot.handlers.start` checks the Telegram user ID.
4. `khata_bot.services.auth` looks up that ID in the `users` table and loads its
   associated shop. The bot replies with the guide only for a registered user.

## Modules

- `khata_bot.config`: loads `.env` with override enabled, validates required
  environment variables, and supplies defaults for optional settings.
- `khata_bot.models`: defines the SQLAlchemy `Shop` and `User` tables.
- `khata_bot.database`: creates the SQLAlchemy engine and session factory and
  applies pending Alembic migrations; SQLite connections use WAL mode and enforce
  foreign keys. It recognizes the original Phase 1 tables and marks that schema
  as migrated, preserving any existing shop and user rows.
- `khata_bot.services.auth`: retrieves a registered user's shop membership.
- `khata_bot.handlers.start`: handles `/start` and gives unauthorized users one
  short denial.
- `khata_bot.bot`: configures logging, Telegram timeouts, the error handler,
  command registration, and the `python -m khata_bot.bot` entry point.
- `tests`: covers settings loading/validation and authorization lookup.

`migrations/` contains the Alembic environment and versioned schema changes.
The bot applies pending migrations before it starts handling updates.
