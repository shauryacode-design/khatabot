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
    "Entry (total price): Aman chini 1kg 45\n"
    "Multiple items: Aman aata 10kg 400\nchini 2kg 100\n"
    "Payment: Aman ne 500 diye\n"
    "Purana balance: Aman pending balance 5000\n"
    "Hisab: /ledger Aman | /sabka_hisab | /today | /month\n"
    "Privacy: customer names, khata entries/payments, and Telegram account IDs "
    "are stored to run the service. Up to eight recent messages are stored "
    "for follow-ups until /forget. Messages may be sent to Groq for AI parsing. "
    "Telegram messages are not end-to-end encrypted."
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

    await message.reply_text(START_GUIDE.format(shop=shop_name or "aapke"))
