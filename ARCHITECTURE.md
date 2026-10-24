# Architecture

Khata Bot is split into a Telegram layer, parsing, and shop-scoped data services.
The database never stores a running balance; the bot calculates it from active
entries and payments whenever a customer asks.

## Message flow

1. `khata_bot.bot` loads and validates `.env`, applies Alembic migrations, and
   builds the Telegram application with long polling, IPv4, generous timeouts,
   error alerts, and the daily encrypted backup task.
2. The handler checks the sender's Telegram ID against the `users` table before
   it reads or changes shop data. `shop_id` is included in every customer and
   ledger query.
3. `khata_bot.parsing.llm_parser` asks Groq for structured JSON understanding.
   Pydantic validates it. `khata_bot.parsing.rules` handles common compact
   Hinglish messages if the API is down or its response is invalid. Casual chat
   receives a conversational reply and is never treated as a saved entry.
4. `khata_bot.handlers.text` routes greetings, customer requests, ledger entries,
   payments, and balance questions. A write is saved as a pending confirmation,
   not applied immediately.
5. The confirmation's database row holds the payload. Telegram buttons contain
   only a short action and row ID. The callback atomically claims the pending
   record, checks its shop/user and 30-minute expiry, writes in a transaction,
   records an audit event, and edits the confirmation message.
6. `khata_bot.services.ledger` calculates balances from non-voided rows. Query
   results are scoped to one shop. Long ledgers have Next/Prev buttons.
7. `khata_bot.handlers.operations` provides CSV export, statements, health,
   corrections, and export-first deletion. Customer records are voided for
   ordinary corrections; shop deletion is the explicit, double-confirmed
   exception.

## Important modules

- `khata_bot.config`: loads `.env` with override enabled, validates required
  values and Fernet keys, and provides typed settings.
- `khata_bot.models`: SQLAlchemy records for shops/users, customers, entries,
  payments, confirmations, invites, audit events, and processed Telegram update
  IDs. Money columns are integer paise.
- `khata_bot.database` and `migrations/`: configure the engine, SQLite WAL and
  foreign keys, migrate schemas at startup, and adopt the original Phase 1
  `shops`/`users` schema without discarding its rows.
- `khata_bot.parsing/`: Pydantic response schema, conservative deterministic
  parser, and asynchronous Groq client. User messages and model output are
  treated as untrusted text.
- `khata_bot.services.auth`: resolves only registered Telegram users.
- `khata_bot.services.customers`: normalizes honorifics and finds exact or fuzzy
  customer matches within one shop.
- `khata_bot.services.ledger`: calculates balances, builds running ledgers,
  writes entries/payments, and records audit events.
- `khata_bot.services.confirmations`: persists Save/Cancel requests and enforces
  their expiry. `idempotency` prevents repeated Telegram update delivery from
  repeating an action.
- `khata_bot.services.backup`: encrypts SQLite snapshots or PostgreSQL dumps,
  enforces 14-day retention, and restores an encrypted backup.
- `khata_bot.services.phones`: encrypts optional phone numbers with Fernet.
- `khata_bot.services.exports`: exports shop customers and ledger data as CSV.
- `khata_bot.handlers.start`, `admin`, `text`, and `operations`: Telegram command,
  natural-language, callback, and maintenance handlers.
- `admin.py`: local command-line shop/user/invite management.

## Money, dates, and safety

Currency is parsed as `Decimal` then stored as integer paise. No float is used for
ledger math. Dates default to the current day in `Asia/Kolkata`; known Hindi
relative dates and common numeric/named dates are supported in the local parser.
An unknown customer is never silently matched to a similar name; the owner must
choose an existing match or confirm a new customer. Prices are never inferred.

`/export` and `/deletemydata` work only for the sender's shop. The latter sends a
CSV first, then requires two callback confirmations before deleting that shop's
records. Database backups use a separate Fernet key from optional phone
encryption.
