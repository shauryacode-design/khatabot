"""Telegram bot application entry point."""

import asyncio
import logging
import re
from datetime import datetime, time as datetime_time, timedelta
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest
from sqlalchemy.orm import Session, sessionmaker

from khata_bot.config import ConfigurationError, Settings, load_settings
from khata_bot.database import create_session_factory
from khata_bot.handlers.start import start_command
from khata_bot.handlers.admin import admin_command
from khata_bot.handlers.text import (
    command_query,
    confirmation_callback,
    customer_command,
    forget_command,
    text_fallback,
    unknown_command,
)
from khata_bot.handlers.operations import (
    delete_my_data_command,
    edit_command,
    edit_value_command,
    export_command,
    health_command,
    statement_command,
    undo_command,
)
from khata_bot.services.backup import create_encrypted_backup
from khata_bot.services.dates import IST

logger = logging.getLogger(__name__)
_TELEGRAM_TOKEN_PATTERN = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b")


def _redact_secrets(value: str) -> str:
    return _TELEGRAM_TOKEN_PATTERN.sub("[REDACTED_TELEGRAM_TOKEN]", value)


def _make_request() -> HTTPXRequest:
    return HTTPXRequest(
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
        httpx_kwargs={
            "transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        },
    )
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    error = context.error
    application = context.application
    failures = application.bot_data.get("consecutive_failures", 0) + 1
    application.bot_data["consecutive_failures"] = failures
    if error is not None:
        logger.error(
            "Unhandled Telegram bot exception: %s",
            _redact_secrets(str(error)),
        )

    if failures >= 3:
        application.bot_data["consecutive_failures"] = 0
        settings = application.bot_data.get("settings")
        if settings is not None:
            try:
                await context.bot.send_message(
                    chat_id=settings.admin_telegram_id,
                    text="Khata Bot has had repeated errors. Please check its logs.",
                )
            except TelegramError as alert_error:
                logger.warning(
                    "Could not send admin alert: %s",
                    _redact_secrets(str(alert_error)),
                )

    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(
                "Network issue, please send that again in a moment."
            )
        except TelegramError as reply_error:
            logger.warning(
                "Could not send the error reply: %s",
                _redact_secrets(str(reply_error)),
            )


async def _daily_backup_loop(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    while True:
        try:
            await asyncio.to_thread(
                create_encrypted_backup,
                settings,
                settings.backup_dir,
            )
            application.bot_data["backup_failures"] = 0
        except (OSError, RuntimeError, ValueError):
            failures = application.bot_data.get("backup_failures", 0) + 1
            application.bot_data["backup_failures"] = failures
            logger.exception("Encrypted database backup failed.")
            if failures >= 3:
                application.bot_data["backup_failures"] = 0
                try:
                    await application.bot.send_message(
                        chat_id=settings.admin_telegram_id,
                        text="Khata Bot backup has failed repeatedly. Please check the database and backup setup.",
                    )
                except TelegramError as alert_error:
                    logger.warning(
                        "Could not send backup alert: %s",
                        _redact_secrets(str(alert_error)),
                    )
        now = datetime.now(IST)
        next_run = datetime.combine(
            now.date() + timedelta(days=1), datetime_time.min, tzinfo=IST
        )
        await asyncio.sleep(max(1, (next_run - now).total_seconds()))


async def _start_background_jobs(application: Application) -> None:
    application.bot_data["backup_task"] = asyncio.create_task(
        _daily_backup_loop(application)
    )


async def _stop_background_jobs(application: Application) -> None:
    task = application.bot_data.get("backup_task")
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def build_application(
    settings: Settings,
    session_factory: sessionmaker[Session] | None = None,
) -> Application:
    """Build the bot application; injection keeps handlers straightforward to test."""
    request = _make_request()
    get_updates_request = _make_request()
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request)
        .get_updates_request(get_updates_request)
        .concurrent_updates(True)
        .post_init(_start_background_jobs)
        .post_shutdown(_stop_background_jobs)
        .build()
    )
    application.bot_data["session_factory"] = (
        session_factory or create_session_factory(settings)
    )
    application.bot_data["settings"] = settings
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("forget", forget_command))
    application.add_handler(CallbackQueryHandler(confirmation_callback))
    for command in ("all", "ledger", "today", "month"):
        application.add_handler(CommandHandler(command, command_query))
    command_handlers = {
        "customer": customer_command,
        "undo": undo_command,
        "edit": edit_command,
        "editvalue": edit_value_command,
        "statement": statement_command,
        "export": export_command,
        "health": health_command,
        "deletemydata": delete_my_data_command,
    }
    for command, handler in command_handlers.items():
        application.add_handler(CommandHandler(command, handler))
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND,
            text_fallback,
        )
    )
    application.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & filters.COMMAND, unknown_command)
    )
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    try:
        settings = load_settings()
    except ConfigurationError as error:
        raise SystemExit(str(error)) from error

    application = build_application(settings)
    start_health_server()
    app.run_polling()
    application.run_polling()


if __name__ == "__main__":
    main()
