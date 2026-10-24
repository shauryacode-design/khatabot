"""The start command."""

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select

from khata_bot.models import Shop
from khata_bot.services.admin import redeem_invite
from khata_bot.services.auth import find_authorized_user
from khata_bot.services.idempotency import claim_update

START_GUIDE = (
    "Namaste! {shop} ke khata mein swagat hai.\n"
    "Entry: Aman - chini 1kg 45 | Payment: Aman paid 500\n"
    "Hisab: Aman ka hisab / sabka hisab\n"
    "Privacy: Telegram messages end-to-end encrypted nahi hote."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    session_factory = context.application.bot_data["session_factory"]
    if not claim_update(session_factory, update.update_id):
        return
    invite_code = context.args[0] if context.args else None
    with session_factory.begin() as session:
        if invite_code:
            redeem_invite(session, invite_code, user.id)
        membership = find_authorized_user(session, user.id)
        shop_name = (
            session.scalar(select(Shop.name).where(Shop.id == membership.shop_id))
            if membership is not None
            else None
        )

    if membership is None:
        await message.reply_text("Not authorized.")
        return

    greeting = f"Namaste! {shop_name} ke khata mein swagat hai.\n"
    await message.reply_text(START_GUIDE.format(shop=shop_name or "aapke"))
