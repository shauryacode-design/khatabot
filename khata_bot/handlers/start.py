"""The start command."""

from telegram import Update
from telegram.ext import ContextTypes

from khata_bot.services.auth import find_authorized_user

START_GUIDE = (
    "Namaste! Khata Bot mein aapka swagat hai.\n"
    "Entry: Aman - chini 1kg 45\n"
    "Payment: Aman paid 500\n"
    "Hisab: Aman ka hisab / sabka hisab\n"
    "Privacy: Telegram messages end-to-end encrypted nahi hote."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    session_factory = context.application.bot_data["session_factory"]
    with session_factory() as session:
        membership = find_authorized_user(session, user.id)

    if membership is None:
        await message.reply_text("Not authorized.")
        return

    await message.reply_text(START_GUIDE)
