"""Telegram-only shop administration restricted to ADMIN_TELEGRAM_ID."""

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import func, select

from khata_bot.models import Shop, User
from khata_bot.services.admin import add_user, create_invite, create_shop
from khata_bot.services.idempotency import claim_update


async def admin_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    settings = context.application.bot_data["settings"]
    if user.id != settings.admin_telegram_id:
        await message.reply_text("Not authorized.")
        return
    if not claim_update(
        context.application.bot_data["session_factory"], update.update_id
    ):
        return

    args = context.args
    if not args or args[0] == "help":
        await message.reply_text(
            "/admin create_shop <name>\n"
            "/admin add_user <shop_id> <telegram_id>\n"
            "/admin list_shops\n"
            "/admin create_invite <shop_id>"
        )
        return
    session_factory = context.application.bot_data["session_factory"]
    try:
        with session_factory.begin() as session:
            action = args[0]
            if action == "create_shop" and len(args) >= 2:
                shop = create_shop(
                    session, " ".join(args[1:]), settings.admin_telegram_id
                )
                reply = f"Shop created: {shop.id} — {shop.name}"
            elif action == "add_user" and len(args) == 3:
                add_user(session, int(args[1]), int(args[2]))
                reply = f"User added to shop {args[1]}."
            elif action == "list_shops" and len(args) == 1:
                shops = session.execute(
                    select(Shop, func.count(User.id))
                    .outerjoin(User, User.shop_id == Shop.id)
                    .group_by(Shop.id)
                    .order_by(Shop.id)
                ).all()
                reply = "\n".join(
                    f"{shop.id}. {shop.name} — {count} user(s)"
                    for shop, count in shops
                ) or "No shops found."
            elif action == "create_invite" and len(args) == 2:
                code = create_invite(
                    session, int(args[1]), settings.admin_telegram_id
                )
                reply = f"One-time invite (expires in 7 days):\n/start {code}"
            else:
                await message.reply_text("Invalid admin command. Use /admin help.")
                return
    except (ValueError, TypeError) as error:
        await message.reply_text(f"Could not complete that request: {error}")
        return
    await message.reply_text(reply)
