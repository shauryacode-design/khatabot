# Project progress

## Completed in this implementation

- Phase 1: environment validation, SQLAlchemy database setup, initial Alembic
  migration, shop membership, `/start`, Telegram request settings, and error
  handling. Existing Phase 1 databases are migrated without dropping their
  shop/user rows.
- Phase 2: compact rule parsing, persistent 30-minute Save/Cancel confirmations,
  callback IDs below Telegram's 64-byte limit, transactional/idempotent saves.
- Phase 3: Groq-first structured parser, Pydantic validation, one invalid-output
  retry, retry without JSON mode when unsupported, and friendly rule fallback.
- Phase 4: customer names, entries, payments, calculated balances, ledger
  pagination, `/all`, `/ledger`, `/today`, `/month`, natural-language queries,
  and customer name matching.
- Phase 5: `/undo`, `/edit`, soft voiding, audit records, monthly statements,
  CSV export, and export-first double-confirmed shop data deletion.
- Phase 6: admin CLI and Telegram admin commands, single-use invites, per-user
  rate limiting for natural-language parsing, Fernet key validation and phone
  encryption helper, encrypted daily backups with 14-day retention, restore
  helper, `/health`, repeated-failure alerts, Docker deployment files, and
  tenant-isolation tests.
- Follow-up fixes: explicit customer-ledger clear requests now require a
  confirmation and retain soft-voided rows for audit; a durable, shop-scoped
  memory keeps up to eight recent user messages and the last customer, with
  `/forget` to clear it. Migration `0003_conversation_memory` applies on startup.
- Documentation updated in `README.md` and `ARCHITECTURE.md`.

## Next

- Phase 7 optional features: saved shop price list, voice note transcription,
  reminders, and language selection.
- Start the bot to apply migration `0003` to the local database; the automated
  migration test verifies the prior shop/user data remains intact.
- Run a live Telegram/Groq smoke test using only the credentials already in the
  local `.env`; tests themselves require no credentials.

## Verification

- `python -m pytest -q`: 100 tests passed.
- `python -m compileall -q khata_bot migrations tests admin.py`: passed.
- `git diff --check`: passed.

## Known limitations

- Live Telegram polling and Groq calls have not been run as part of automated
  tests; the Groq contract is covered with mocked HTTP responses.
- PostgreSQL connectivity and `pg_dump`/`psql` restore need a configured
  PostgreSQL service/client tools to integration-test.
- LLM responses can still misunderstand an informal message. All entry and
  payment writes show a review screen; check customer, date, quantity, and price
  before saving.
- Voice notes, reminders, saved prices, and language switching are not
  implemented yet.

## Commands

- Install pinned dependencies: `python -m pip install -r requirements.txt`
- Run tests: `python -m pytest`
- Start bot: `python -m khata_bot.bot`
- Admin help: `python admin.py --help`
- Create a shop: `python admin.py create_shop "Shop name"`
- Check shops: `python admin.py list_shops`
- Docker: `docker compose up --build -d`
