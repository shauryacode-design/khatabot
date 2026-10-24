# Khata Bot

Khata Bot is a Telegram khata assistant for local Indian shops. It accepts
ordinary Hinglish, Hindi, and English messages, replies to casual chat, and
requires a Save/Cancel tap before changing customer balances.

## Local setup (Python 3.12)

1. Install dependencies into the same Python interpreter you use to start the
   bot:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`. Set the BotFather token, your numeric
   `ADMIN_TELEGRAM_ID`, a Groq API key, and two **different** valid Fernet keys.
   Generate each key separately:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Do not paste credentials in chat or commit `.env`. Groq and Telegram
   credentials are read locally from `.env`; there is no test requirement to
   share them.

3. Create the initial shop. `create_shop` automatically registers the configured
   admin when that Telegram account is not already assigned to a shop:

   ```powershell
   python admin.py create_shop "Demo shop"
   ```

   Invite another shop user with either:

   ```powershell
   python admin.py add_user <shop_id> <telegram_id>
   python admin.py create_invite <shop_id>
   ```

   A one-time invite expires after seven days. Send the printed `/start CODE`
   command to the new shop user. Use `python admin.py --help` for all options.

4. Start from the project root:

   ```powershell
   python -m khata_bot.bot
   ```

   The bot applies Alembic migrations at startup and creates an encrypted
   database backup daily. For a manually created Telegram bot, start a private
   chat and send `/start`.

## Messages to try

- `Hello` — a friendly reply with khata guidance.
- `Aman - chini 1kg 45, aata 10kg 400` — review the detected customer, date,
  items, quantities, and total; each final number is that item's total price,
  not a per-unit rate. Tap Save or Cancel.
- `Aman aata 10kg 400` followed by `chini 2kg 100` on the next line — the
  second line inherits the customer name. The bot shows each saved line and
  its running balance.
- `Aman ne 500 diye` — confirm a payment before it changes the balance.
- `Aman chini 1kg -45`, `0`, or an unreadable price such as `10pct` is rejected;
  no amount is guessed or saved.
- `Shaurya pending balance 5000` — import existing customer balances; send
  several customers as one per line. The bot previews the complete batch and
  adds it only after you tap Save. Exact existing customer names are matched;
  similar names must be corrected before import.
- `Aman ka hisab`, `sabka hisab`, `aaj ka hisab` — view a customer ledger,
  outstanding balances sorted by amount owed, or today's entries.
- `Shaurya aata 10kg 250, chinni 1kg 100` — multiple items on one entry.
- `add new customer Shaurya` or `/customer Shaurya` — confirm adding a customer.

Natural-language parsing uses Groq as the primary interpreter and a conservative
rule parser if Groq is unavailable. An unavailable AI model falls back to rules
and asks the owner to verify parsed fields before saving. Never accept a guessed
price or customer name; provide the missing detail instead. In date phrases,
`kal` means yesterday; use `tomorrow` for the next day.

## Commands

- `/start` — guide and privacy note.
- `/all`, `/ledger <name>`, `/today`, `/month [month]` — balances and ledgers.
- `/sabka_hisab` — alias for `/all`.
- `/customer <name>` — add a customer with confirmation. If two customers have
  the same name, add a distinguishing label as `/customer Aman | Main bazaar`.
- `/undo`, `/edit` — correct recent records without hard-deleting ledger rows.
- `/statement <name> [month]`, `/statementpdf <name | label>`, `/export`,
  `/search <partial name>` — statements, shareable customer PDF, full shop CSV,
  and partial-name search. The CSV includes the same outstanding total shown
  by `/all`; the PDF transliterates names and items into Latin characters.
- `/health`, `/deletemydata` — status and export-first, double-confirmed deletion.
- `Avinash ka hisab delete krdo` — asks for explicit confirmation before clearing
  that customer's ledger; the customer remains and cleared rows are soft-voided
  for audit.
- `/forget` — clear the recent conversation memory without affecting ledger data.
- `/admin help` — Telegram admin tools for the configured admin ID.

All ledger reads and writes are scoped to the user's shop. Money is stored as
integer paise; customer balances are calculated from active entries less
payments. Timestamps are UTC-aware internally; business dates are interpreted
in IST. The bot stores customer names, ledger entries/payments, and Telegram
account IDs for authorization and audit. Up to eight recent messages are kept
until `/forget`; messages may be sent to Groq for AI parsing. Telegram messages
are not end-to-end encrypted. The command menu is registered with Telegram on
startup; BotFather may also be configured manually to show the same commands.

The bot persists up to eight recent user messages and the last explicitly
mentioned customer per authorized user/shop to understand follow-ups such as
`chawal 300` followed by another item. `/forget` clears this conversational
context; ledger records are separate.

## Tests

```powershell
python -m pytest
```

Tests do not make live Telegram or Groq calls. They cover the mocked Groq API,
parser examples, date and money handling, ledger isolation, confirmations,
idempotent callbacks, migration compatibility, encrypted backup/restore,
customer-label matching, and admin invites. To exercise PostgreSQL, set
`POSTGRES_TEST_DATABASE_URL` to a dedicated disposable test database and run
`python -m pytest tests/test_postgres.py`; the tests create and clean up only
their own shop rows.

## Docker and VPS

Put `.env` beside `docker-compose.yml`, then build and start:

```bash
docker compose up --build -d
docker compose logs -f khata-bot
```

The compose file mounts separate persistent volumes for `/data` and `/backups`
and uses the `always` restart policy. To administer shops in the container:

```bash
docker compose run --rm khata-bot python admin.py list_shops
```

On a VPS, restrict SSH access, keep Docker and the host patched, use a strong
unique `.env`, and keep an off-server copy of encrypted backups. PostgreSQL can
be used by setting `DATABASE_URL` to a `postgresql+psycopg://...` URL; install
the PostgreSQL client tools (`pg_dump` and `psql`) on that host for encrypted
database backups and restores.

## Backup and restore

Encrypted backup files are written to `BACKUP_DIR` (default `backups`) and kept
for 14 days. SQLite snapshots use SQLite's online backup API. PostgreSQL
snapshots use `pg_dump`; restoring uses `psql` and the configured `DATABASE_URL`.
The backup encryption key is required to restore.

To test a restore without overwriting the live SQLite database, use
`restore_encrypted_backup(settings, backup_file, restore_to=...)` with a separate
destination path. For a live restore, stop the bot first, keep a copy of the
current database, restore to the configured database, then restart and verify
`/health`, `/all`, and customer ledgers. Do not restore over a running database.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for a plain-language module and data-flow
guide, and [PROGRESS.md](./PROGRESS.md) for current status.
