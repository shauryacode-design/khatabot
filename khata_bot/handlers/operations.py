"""Administrative ledger commands: export, statements, undo, and edits."""

from datetime import date
from io import BytesIO

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.ext import ContextTypes
from sqlalchemy import select

from sqlalchemy.exc import SQLAlchemyError

from khata_bot.handlers.text import _save_keyboard
from khata_bot.models import Customer, Entry, Payment
from khata_bot.services.auth import find_authorized_user
from khata_bot.services.confirmations import create_confirmation
from khata_bot.services.customers import customer_matches
from khata_bot.services.dates import month_bounds, parse_date, today_ist
from khata_bot.services.exports import export_shop_csv
from khata_bot.services.idempotency import claim_update
from khata_bot.services.ledger import ledger_rows
from khata_bot.services.money import format_paise


async def _membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return None
    with context.application.bot_data["session_factory"]() as session:
        membership = find_authorized_user(session, user.id)
        return (
            (membership.id, membership.shop_id, membership.telegram_user_id)
            if membership is not None
            else None
        )


def _claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return claim_update(
        context.application.bot_data["session_factory"], update.update_id
    )


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    membership = await _membership(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not _claim(update, context):
        return
    user_id, shop_id, _telegram_id = membership
    session_factory = context.application.bot_data["session_factory"]
    with session_factory.begin() as session:
        entries = session.scalars(
            select(Entry)
            .where(Entry.shop_id == shop_id, Entry.voided_at.is_(None))
            .order_by(Entry.created_at.desc(), Entry.id.desc())
            .limit(1)
        ).all()
        payments = session.scalars(
            select(Payment)
            .where(Payment.shop_id == shop_id, Payment.voided_at.is_(None))
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(1)
        ).all()
        candidates = []
        if entries:
            candidates.append(
                (entries[0].created_at, "entry", entries[0].id, entries[0].customer_id)
            )
        if payments:
            candidates.append(
                (payments[0].created_at, "payment", payments[0].id, payments[0].customer_id)
            )
        if not candidates:
            await message.reply_text("Undo karne ke liye koi active entry nahi hai.")
            return
        _created, kind, row_id, customer_id = max(candidates, key=lambda item: (item[0], item[2]))
        customer = session.scalar(
            select(Customer).where(Customer.id == customer_id, Customer.shop_id == shop_id)
        )
        label = customer.name if customer else "customer"
        confirmation = create_confirmation(
            session,
            shop_id,
            user_id,
            "undo",
            {"record_type": kind, "record_id": row_id},
        )
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "↩ Confirm undo", callback_data=f"undo:{confirmation.id}"
            ),
            InlineKeyboardButton(
                "Cancel", callback_data=f"cancel:{confirmation.id}"
            ),
        ]]
    )
    await message.reply_text(
        f"Recent {kind} for {label} ko void karna hai? Isse balance dobara calculate hoga.",
        reply_markup=keyboard,
    )


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    membership = await _membership(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not _claim(update, context):
        return
    _user_id, shop_id, _telegram_id = membership
    with context.application.bot_data["session_factory"]() as session:
        entries = session.scalars(
            select(Entry)
            .where(Entry.shop_id == shop_id, Entry.voided_at.is_(None))
            .order_by(Entry.created_at.desc(), Entry.id.desc())
            .limit(10)
        ).all()
        if not entries:
            await message.reply_text("Edit karne ke liye koi active entry nahi hai.")
            return
        buttons = []
        for entry in entries:
            customer = session.scalar(
                select(Customer).where(
                    Customer.id == entry.customer_id, Customer.shop_id == shop_id
                )
            )
            label = (
                f"{customer.name if customer else 'Customer'} — "
                f"{entry.item_name} {format_paise(entry.price_paise)} "
                f"({_format_date(entry.entry_date)})"
            )
            buttons.append(
                InlineKeyboardButton(label[:60], callback_data=f"edit:{entry.id}")
            )
    await message.reply_text(
        "Kaunsi entry badalni hai?",
        reply_markup=InlineKeyboardMarkup([[button] for button in buttons]),
    )


def _format_date(value: date) -> str:
    return value.strftime("%d %b")


async def edit_value_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None or not context.args:
        if message:
            await message.reply_text("Use: /editvalue <request_id> <new value>")
        return
    membership = await _membership(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not _claim(update, context):
        return
    user_id, shop_id, _telegram_id = membership
    try:
        confirmation_id = int(context.args[0])
    except ValueError:
        await message.reply_text("Request ID number hona chahiye.")
        return
    new_value = " ".join(context.args[1:]).strip()
    if not new_value:
        await message.reply_text("Nayi value bhi likhiye.")
        return
    session_factory = context.application.bot_data["session_factory"]
    request_expired = False
    with session_factory.begin() as session:
        from khata_bot.models import PendingConfirmation

        confirmation = session.scalar(
            select(PendingConfirmation).where(
                PendingConfirmation.id == confirmation_id,
                PendingConfirmation.shop_id == shop_id,
                PendingConfirmation.user_id == user_id,
                PendingConfirmation.kind == "edit_value",
                PendingConfirmation.status == "awaiting_input",
            )
        )
        if confirmation is None:
            await message.reply_text("Yeh edit request nahi mila ya expire ho gaya.")
            return
        from khata_bot.services.confirmations import expired

        if expired(confirmation):
            confirmation.status = "expired"
            request_expired = True
        else:
            payload = confirmation.payload
            field = payload["field"]
            if field == "price":
                from khata_bot.services.money import rupees_to_paise

                try:
                    payload["new_value"] = rupees_to_paise(new_value)
                except ValueError as error:
                    await message.reply_text(str(error))
                    return
                display_value = format_paise(payload["new_value"])
            elif field == "date":
                parsed_date = parse_date(new_value)
                if parsed_date is None:
                    await message.reply_text(
                        "Date samajh nahi aayi. Example: 12 Oct 2026."
                    )
                    return
                payload["new_value"] = parsed_date.isoformat()
                display_value = _format_date(parsed_date)
            else:
                payload["new_value"] = new_value[:160]
                display_value = payload["new_value"]
            confirmation.payload = payload
            confirmation.kind = "edit_change"
            confirmation.status = "pending"
            preview = (
                f"Entry {payload['entry_id']} ka {field} "
                f"{display_value} karna hai? Save?"
            )
    if request_expired:
        await message.reply_text("Yeh edit request expire ho gayi. /edit dobara bhejiye.")
        return
    await message.reply_text(
        preview,
        reply_markup=_save_keyboard(confirmation_id),
    )


async def statement_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    membership = await _membership(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not _claim(update, context):
        return
    _user_id, shop_id, _telegram_id = membership
    if not context.args:
        await message.reply_text("Use: /statement <customer name> [month, e.g. Oct 2026]")
        return
    args = context.args
    month_start = None
    month_index = next(
        (index for index, value in enumerate(args) if value[:3].casefold() in {
            "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"
        }),
        None,
    )
    if month_index is not None:
        parsed_month = parse_date("1 " + " ".join(args[month_index:]))
        if parsed_month is None:
            await message.reply_text("Month samajh nahi aaya. Example: /statement Aman Oct 2026")
            return
        month_start = month_bounds(parsed_month.year, parsed_month.month)
        customer_name = " ".join(args[:month_index])
    else:
        customer_name = " ".join(args)
        current = today_ist()
        month_start = month_bounds(current.year, current.month)

    with context.application.bot_data["session_factory"]() as session:
        matches = customer_matches(session, shop_id, customer_name)
        if not matches:
            await message.reply_text(f"{customer_name} naam ka customer nahi mila.")
            return
        customer = matches[0]
        rows = ledger_rows(session, shop_id, customer.id)
        start, end = month_start
        rows = [row for row in rows if start <= row["date"] < end]
        total = 0
        lines = [f"{customer.name} — {start.strftime('%B %Y')} statement"]
        entry_lines = []
        for row in rows:
            total += row["amount"]
            label = "Payment" if row["kind"] == "payment" else row["description"]
            entry_lines.append(
                f"{_format_date(row['date'])}  {label}  "
                f"{format_paise(abs(row['amount']))}"
            )
        lines.extend(
            f"{line[:120]}" for line in entry_lines[:30]
        )
        if len(entry_lines) > 30:
            lines.append(f"... aur {len(entry_lines) - 30} entries hain.")
        lines.extend(
            [
                f"Month net: {format_paise(total)}",
                f"Pending balance: {format_paise(sum(row['amount'] for row in ledger_rows(session, shop_id, customer.id) if row['date'] < end))}",
            ]
        )
    await message.reply_text("\n".join(lines))


async def export_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    membership = await _membership(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not _claim(update, context):
        return
    _user_id, shop_id, _telegram_id = membership
    with context.application.bot_data["session_factory"]() as session:
        data = export_shop_csv(
            session,
            shop_id,
            context.application.bot_data["settings"].encryption_key,
        )
    await message.reply_document(
        document=InputFile(BytesIO(data), filename="khata-export.csv"),
        caption="Aapke shop ka CSV export.",
    )


async def health_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    membership = await _membership(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not _claim(update, context):
        return
    try:
        with context.application.bot_data["session_factory"]() as session:
            session.execute(select(1))
    except SQLAlchemyError:
        import logging

        logging.getLogger(__name__).exception("Health check database query failed.")
        await message.reply_text("Database abhi available nahi hai.")
        return
    await message.reply_text("Bot aur database theek kaam kar rahe hain. ✅")


async def delete_my_data_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    membership = await _membership(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not _claim(update, context):
        return
    user_id, shop_id, _telegram_id = membership
    session_factory = context.application.bot_data["session_factory"]
    with session_factory.begin() as session:
        data = export_shop_csv(
            session,
            shop_id,
            context.application.bot_data["settings"].encryption_key,
        )
        confirmation = create_confirmation(
            session,
            shop_id,
            user_id,
            "delete_stage1",
            {"requested_by": user_id},
        )
    await message.reply_document(
        document=InputFile(BytesIO(data), filename="khata-before-delete.csv"),
        caption=(
            "Pehle apna export download karein. Iske baad shop ka poora khata "
            "delete karne ke liye do baar confirm karna hoga."
        ),
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "I downloaded the export",
                    callback_data=f"deleteprepare:{confirmation.id}",
                )
            ]]
        ),
    )
