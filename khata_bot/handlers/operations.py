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
from khata_bot.services.customers import (
    customer_display_name,
    customer_matches,
    normalize_name,
)
from khata_bot.services.dates import month_bounds, parse_date, today_ist
from khata_bot.services.exports import (
    export_customer_statement_pdf,
    export_shop_csv,
)
from khata_bot.services.idempotency import claim_update
from khata_bot.services.ledger import customer_balance, ledger_rows
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
        label = customer_display_name(customer) if customer else "customer"
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
        payments = session.scalars(
            select(Payment)
            .where(Payment.shop_id == shop_id, Payment.voided_at.is_(None))
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(10)
        ).all()
        if not entries and not payments:
            await message.reply_text("Edit karne ke liye koi active record nahi hai.")
            return
        buttons = []
        for entry in entries:
            customer = session.scalar(
                select(Customer).where(
                    Customer.id == entry.customer_id, Customer.shop_id == shop_id
                )
            )
            label = (
                f"{customer_display_name(customer) if customer else 'Customer'} — "
                f"{entry.item_name} {format_paise(entry.price_paise)} "
                f"({_format_date(entry.entry_date)})"
            )
            buttons.append(
                InlineKeyboardButton(label[:60], callback_data=f"edit:{entry.id}")
            )
        for payment in payments:
            customer = session.scalar(
                select(Customer).where(
                    Customer.id == payment.customer_id,
                    Customer.shop_id == shop_id,
                )
            )
            label = (
                f"{customer_display_name(customer) if customer else 'Customer'} — "
                f"Payment {format_paise(payment.amount_paise)} "
                f"({_format_date(payment.payment_date)})"
            )
            buttons.append(
                InlineKeyboardButton(
                    label[:60], callback_data=f"editpay:{payment.id}"
                )
            )
    await message.reply_text(
        "Kaunsa record badalna hai?",
        reply_markup=InlineKeyboardMarkup([[button] for button in buttons]),
    )


def _format_date(value: date) -> str:
    return value.strftime("%d %b %Y")


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
                if payload.get("record_type") == "payment":
                    payment = session.scalar(
                        select(Payment).where(
                            Payment.id == payload["payment_id"],
                            Payment.shop_id == shop_id,
                            Payment.voided_at.is_(None),
                        )
                    )
                    if payment is None:
                        await message.reply_text("Yeh payment ab active nahi hai.")
                        return
                    balance_before_payment = (
                        customer_balance(session, shop_id, payment.customer_id)
                        + payment.amount_paise
                    )
                    payload["advance_warning"] = payload["new_value"] > max(
                        balance_before_payment, 0
                    )
            elif field == "customer":
                matches = customer_matches(session, shop_id, new_value)
                exact = (
                    matches
                    if "|" in new_value
                    else [
                        candidate
                        for candidate in matches
                        if normalize_name(candidate.name)
                        == normalize_name(new_value)
                    ]
                )
                if len(exact) != 1:
                    if exact:
                        options = ", ".join(
                            customer_display_name(candidate) for candidate in exact
                        )
                        await message.reply_text(
                            "Customer naam ambiguous hai. Exact label ke saath "
                            f"dobara bhejiye: {options}"
                        )
                    else:
                        await message.reply_text(
                            "Customer ka exact naam nahi mila. Milte-julte naam "
                            "par edit save nahi hoga; sahi naam ya Name | Label bhejiye."
                        )
                    return
                payload["new_value"] = exact[0].id
                payload["new_customer_name"] = customer_display_name(exact[0])
                display_value = payload["new_customer_name"]
                if payload.get("record_type") == "payment":
                    payment = session.scalar(
                        select(Payment).where(
                            Payment.id == payload["payment_id"],
                            Payment.shop_id == shop_id,
                            Payment.voided_at.is_(None),
                        )
                    )
                    if payment is None:
                        await message.reply_text("Yeh payment ab active nahi hai.")
                        return
                    target_balance = customer_balance(
                        session, shop_id, exact[0].id
                    )
                    if exact[0].id == payment.customer_id:
                        target_balance += payment.amount_paise
                    payload["advance_warning"] = payment.amount_paise > max(
                        target_balance, 0
                    )
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
            record_name = (
                "Payment" if payload.get("record_type") == "payment" else "Entry"
            )
            record_id = payload.get("payment_id", payload.get("entry_id"))
            preview = (
                f"{record_name} {record_id} ka {field} "
                f"{display_value} karna hai? Save?"
            )
            if payload.get("advance_warning"):
                preview += (
                    "\n⚠️ Isse customer ka advance/overpayment balance ho sakta hai."
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
        await message.reply_text(
            "Use: /statement <customer name> [month, e.g. Oct 2026]"
        )
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
        heading = f"{parsed_month.strftime('%B %Y')} statement"
    else:
        customer_name = " ".join(args)
        month_start = (date.min, date.max)
        heading = "Full statement"

    with context.application.bot_data["session_factory"]() as session:
        matches = customer_matches(session, shop_id, customer_name)
        if not matches:
            await message.reply_text(f"{customer_name} naam ka customer nahi mila.")
            return
        exact = (
            matches
            if "|" in customer_name
            else [
                item for item in matches
                if normalize_name(item.name) == normalize_name(customer_name)
            ]
        )
        if len(exact) != 1:
            options = "\n".join(
                f"• {customer_display_name(item)}" for item in matches[:5]
            )
            await message.reply_text(
                "Exact customer select karne ke liye /statement Name | Label "
                "likhiye. Mumkin matches:\n" + options
            )
            return
        customer = exact[0]
        rows = ledger_rows(session, shop_id, customer.id)
        start, end = month_start
        rows = [row for row in rows if start <= row["date"] < end]
        lines = [
            f"{customer_display_name(customer)} — {heading}",
            "Date | Item/payment | Amount | Running balance",
        ]
        for row in rows:
            label = "Payment" if row["kind"] == "payment" else row["description"]
            amount = format_paise(abs(row["amount"]))
            signed_amount = f"-{amount}" if row["amount"] < 0 else f"+{amount}"
            lines.append(
                f"{_format_date(row['date'])} | {label} | {signed_amount} | "
                f"{format_paise(row['balance'])}"
            )
        if not rows:
            lines.append("Is period mein koi active transaction nahi hai.")
        lines.extend(
            [
                f"Pending balance: {format_paise(sum(row['amount'] for row in ledger_rows(session, shop_id, customer.id) if row['date'] < end))}",
            ]
        )
    for index in range(0, len(lines), 25):
        await message.reply_text("\n".join(lines[index : index + 25]))


async def statement_pdf_command(
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
        await message.reply_text("Use: /statementpdf <customer name> [| label]")
        return
    customer_query = " ".join(context.args)
    with context.application.bot_data["session_factory"]() as session:
        matches = customer_matches(session, shop_id, customer_query)
        exact = (
            matches
            if "|" in customer_query
            else [
                item for item in matches
                if normalize_name(item.name) == normalize_name(customer_query)
            ]
        )
        if len(exact) != 1:
            if not matches:
                await message.reply_text(
                    f"{customer_query} naam ka customer nahi mila."
                )
            else:
                await message.reply_text(
                    "Sahi customer ke liye label ke saath bhejiye: "
                    + ", ".join(customer_display_name(item) for item in matches[:5])
                )
            return
        customer = exact[0]
        data = export_customer_statement_pdf(session, shop_id, customer.id)
        display_name = customer_display_name(customer)
    await message.reply_document(
        document=InputFile(BytesIO(data), filename="customer-statement.pdf"),
        caption=f"{display_name} ka khata statement.",
    )


async def search_command(
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
    query = " ".join(context.args).strip()
    if not query:
        await message.reply_text("Use: /search <partial customer name>")
        return
    normalized_query = query.casefold()
    with context.application.bot_data["session_factory"]() as session:
        customers = session.scalars(
            select(Customer)
            .where(
                Customer.shop_id == shop_id,
                Customer.normalized_name.contains(normalized_query),
            )
            .order_by(Customer.name, Customer.label)
            .limit(30)
        ).all()
        if not customers:
            await message.reply_text("Is naam se koi customer nahi mila.")
            return
        results = [
            f"{customer_display_name(customer)}: "
            f"{format_paise(sum(row['amount'] for row in ledger_rows(session, shop_id, customer.id)))}"
            for customer in customers
        ]
    await message.reply_text("Customer search:\n" + "\n".join(results))


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
    except SQLAlchemyError as error:
        import logging

        logging.getLogger(__name__).error(
            "Health check database query failed (type=%s).",
            type(error).__name__,
        )
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
