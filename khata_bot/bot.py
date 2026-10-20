"""Telegram bot application entry point."""

import logging
import re

import httpx
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
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
from khata_bot.handlers.text import text_fallback

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


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    error = context.error
    if error is not None:
        logger.error(
            "Unhandled Telegram bot exception: %s",
            _redact_secrets(str(error)),
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
        .build()
    )
    application.bot_data["session_factory"] = (
        session_factory or create_session_factory(settings)
    )
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND,
            text_fallback,
        )
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
    application.run_polling()


if __name__ == "__main__":
    main()
