# Khata Bot

Khata Bot is a Telegram assistant for shopkeepers to record customer credit and
payments using everyday messages. This is the Phase 1 foundation; entry parsing
and ledger features will be added in later phases.

## Phase 1 setup

1. Use Python 3.12 and install the pinned dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`. Add the BotFather token, numeric admin Telegram
   ID, Groq API key, and two different Fernet keys. Generate each key with:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Do not paste credentials into chat or commit `.env`.

3. The bot applies Alembic migrations at startup. Add an initial shop and its
   authorized Telegram user to the `shops` and `users` tables. The Phase 1 bot
   intentionally rejects unregistered users; the admin CLI is planned for a
   later phase.

4. Start the bot from the project root:

   ```powershell
   python -m khata_bot.bot
   ```

Run the Phase 1 tests with `python -m pytest`.

## Current capabilities

- Loads `.env` before process environment values and reports missing settings.
- Creates the SQLite database with WAL and foreign-key enforcement enabled.
- Checks shop membership before replying to `/start`.
- Replies to ordinary messages with a Phase 1 status instead of silently
  ignoring entry and ledger requests that are not implemented yet.
- Uses long-polling request timeouts and registers a global error handler.

See [ARCHITECTURE.md](./ARCHITECTURE.md) and [PROGRESS.md](./PROGRESS.md).
