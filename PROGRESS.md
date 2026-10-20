# Project progress

## Done

- Phase 1: created the Python package, validated environment configuration,
  SQLite database setup, shop/user models, registered-user check, `/start`,
  Telegram request timeouts, a global error handler, and the initial Alembic
  migration. Existing databases from the earlier `create_all` startup are
  recognized and adopted by the initial migration without removing data.
- Ordinary text now receives a friendly Phase 1 status reply for registered
  users; unknown users still receive only the authorization denial.
- Added initial tests, setup instructions, and a plain-language architecture
  overview.

## Next

- Phase 2: rule-based entry parsing and persistent Save/Cancel confirmations.

## Known limitations

- No shop/user admin CLI exists yet, so a shop and an authorized user must be
  inserted into the database manually before `/start` can be used.
- Credentials are required to run the live bot, but none are required for tests.

## Commands

- Install dependencies: `python -m pip install -r requirements.txt`
- Run tests: `python -m pytest`
- Start the bot: `python -m khata_bot.bot`
