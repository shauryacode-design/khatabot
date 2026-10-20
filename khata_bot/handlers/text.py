"""Fallback response for ordinary text before message features are implemented."""

from telegram import Update
from telegram.ext import ContextTypes

from khata_bot.services.auth import find_authorized_user

PHASE_ONE_REPLY = (
    "Abhi bot setup ke Phase 1 mein hai. Filhaal /start kaam karta hai; "
    "entry aur hisab features abhi available nahi hain."
)


async def text_fallback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or not message.text or user is None:
        return

    session_factory = context.application.bot_data["session_factory"]
    with session_factory() as session:
        membership = find_authorized_user(session, user.id)

    if membership is None:
        await message.reply_text("Not authorized.")
        return

    await message.reply_text(PHASE_ONE_REPLY)
