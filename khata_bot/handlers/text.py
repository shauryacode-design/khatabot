"""Natural-language chat, ledger confirmations, and customer queries."""

import logging
import re
import time
from collections import deque
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
import httpx

from khata_bot.config import Settings
from khata_bot.models import Customer, Entry, Payment, PendingConfirmation
from khata_bot.parsing.llm_parser import parse_message
from khata_bot.parsing.rules import parse_with_rules
from khata_bot.parsing.schemas import ParsedMessage
from khata_bot.services.confirmations import (
    claim_pending_confirmation,
    create_confirmation,
    expired,
    get_pending_confirmation,
)
from khata_bot.services.customers import customer_matches, normalize_name
from khata_bot.services.dates import month_bounds, today_ist
from khata_bot.services.deletion import delete_exported_shop_data
from khata_bot.services.ledger import (
    all_balances,
    audit,
    customer_balance,
    ledger_rows,
    record_entry,
    record_payment,
    utc_now_naive,
    void_customer_ledger,
)
from khata_bot.services.conversation import (
    clear_conversation_memory,
    remember_customer,
    remember_user_message,
)
from khata_bot.services.idempotency import claim_update
from khata_bot.services.money import format_paise, rupees_to_paise
from khata_bot.services.auth import find_authorized_user

logger = logging.getLogger(__name__)
_LEDGER_PAGE_SIZE = 8
_RATE_LIMIT_STATE_KEY = "rate_limit_events"


def _rate_limited(context: ContextTypes.DEFAULT_TYPE, user_id: int, limit: int) -> bool:
    buckets = context.application.bot_data.setdefault(_RATE_LIMIT_STATE_KEY, {})
    now = time.monotonic()
    events = buckets.setdefault(user_id, deque())
    while events and now - events[0] >= 60:
        events.popleft()
    if len(events) >= limit:
        return True
    events.append(now)
    return False


async def _authorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return None
    session_factory = context.application.bot_data["session_factory"]
    with session_factory() as session:
        membership = find_authorized_user(session, user.id)
        if membership is None:
            return None
        return membership.id, membership.shop_id, membership.telegram_user_id


def _format_date(value: date) -> str:
    return value.strftime("%d %b %Y")


def _confirmation_text(payload: dict) -> str:
    date_value = date.fromisoformat(payload["date"])
    title = payload["customer_name"]
    lines = [f"{title} | {_format_date(date_value)}"]
    if payload["kind"] == "payment":
        lines.extend(
            [f"Payment {format_paise(payload['amount_paise'])}", "Save this payment?"]
        )
    else:
        total = 0
        for item in payload["items"]:
            item_line = item["name"]
            if item.get("quantity"):
                item_line += f" {item['quantity']}"
            item_line += f" {format_paise(item['price_paise'])}"
            total += item["price_paise"]
            lines.append(item_line)
        lines.extend([f"Total {format_paise(total)}", "Save this entry?"])
    if payload.get("new_customer"):
        lines.insert(1, "New customer")
    return "\n".join(lines)


def _save_keyboard(confirmation_id: int) -> InlineKeyboardMarkup:
    save_data = f"save:{confirmation_id}"
    cancel_data = f"cancel:{confirmation_id}"
    if any(len(value.encode("utf-8")) > 64 for value in (save_data, cancel_data)):
        raise ValueError("Confirmation callback data exceeds Telegram's 64-byte limit.")
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Save", callback_data=save_data),
            InlineKeyboardButton("❌ Cancel", callback_data=cancel_data),
        ]]
    )


def _ledger_text(customer: Customer, rows: list[dict], page: int) -> tuple[str, InlineKeyboardMarkup | None]:
    start = page * _LEDGER_PAGE_SIZE
    visible = rows[start : start + _LEDGER_PAGE_SIZE]
    if not rows:
        return f"{customer.name} ka khata abhi khaali hai.", None
    lines = [f"{customer.name} ka khata"]
    for row in visible:
        amount = format_paise(abs(row["amount"]))
        if row["kind"] == "payment":
            summary = f"  Payment −{amount}"
        else:
            summary = f"  {row['description']} +{amount}"
        lines.append(f"{_format_date(row['date'])}{summary}")
    lines.append(f"Balance: {format_paise(rows[-1]['balance'])}")
    if len(rows) <= _LEDGER_PAGE_SIZE:
        return "\n".join(lines), None
    buttons = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                "⬅ Prev", callback_data=f"page:{customer.id}:{page - 1}"
            )
        )
    if start + _LEDGER_PAGE_SIZE < len(rows):
        buttons.append(
            InlineKeyboardButton(
                "Next ➡", callback_data=f"page:{customer.id}:{page + 1}"
            )
        )
    return "\n".join(lines), InlineKeyboardMarkup([buttons])


async def _show_customer_ledger(
    message, session, shop_id: int, customer: Customer, page: int = 0
) -> None:
    rows = ledger_rows(session, shop_id, customer.id)
    text, keyboard = _ledger_text(customer, rows, page)
    await message.reply_text(text, reply_markup=keyboard)


async def _send_all_balances(message, session, shop_id: int) -> None:
    balances = all_balances(session, shop_id)
    if not balances:
        await message.reply_text("Abhi kisi customer ka udhaar baaki nahi hai. ✅")
        return
    lines = ["Sabhi customers ka baaki hisaab:"]
    for customer, balance in balances[:45]:
        lines.append(f"{customer.name}: {format_paise(balance)}")
    lines.append(f"Total baaki: {format_paise(sum(item[1] for item in balances))}")
    await message.reply_text("\n".join(lines))


async def _send_period(
    message,
    session,
    shop_id: int,
    start: date,
    end: date,
    heading: str,
) -> None:
    entries = session.scalars(
        select(Entry, Customer)
        .join(Customer, Customer.id == Entry.customer_id)
        .where(
            Entry.shop_id == shop_id,
            Customer.shop_id == shop_id,
            Entry.voided_at.is_(None),
            Entry.entry_date >= start,
            Entry.entry_date < end,
        )
        .order_by(Entry.entry_date, Entry.id)
    ).all()
    payments = session.scalars(
        select(Payment, Customer)
        .join(Customer, Customer.id == Payment.customer_id)
        .where(
            Payment.shop_id == shop_id,
            Customer.shop_id == shop_id,
            Payment.voided_at.is_(None),
            Payment.payment_date >= start,
            Payment.payment_date < end,
        )
        .order_by(Payment.payment_date, Payment.id)
    ).all()
    if not entries and not payments:
        await message.reply_text(f"{heading}: is period mein koi entry nahi hai.")
        return
    lines = [heading]
    total_entries = 0
    total_payments = 0
    merged = [
        (entry.entry_date, entry.id, "entry", customer.name, entry.price_paise, entry.item_name)
        for entry, customer in entries
    ]
    merged.extend(
        (payment.payment_date, payment.id, "payment", customer.name, payment.amount_paise, "Payment")
        for payment, customer in payments
    )
    for event_date, _row_id, kind, customer_name, amount, description in sorted(merged):
        if kind == "entry":
            total_entries += amount
            lines.append(f"{_format_date(event_date)} {customer_name}: {description} {format_paise(amount)}")
        else:
            total_payments += amount
            lines.append(f"{_format_date(event_date)} {customer_name}: payment {format_paise(amount)}")
    lines.extend(
        [
            f"Goods total: {format_paise(total_entries)}",
            f"Payments total: {format_paise(total_payments)}",
            f"Net change: {format_paise(total_entries - total_payments)}",
        ]
    )
    await message.reply_text("\n".join(lines[:50]))


async def _reply_to_query(
    message,
    session,
    shop_id: int,
    parsed: ParsedMessage,
) -> None:
    if parsed.query_type == "all":
        await _send_all_balances(message, session, shop_id)
        return
    if parsed.query_type == "today":
        current = parsed.date or today_ist()
        await _send_period(
            message,
            session,
            shop_id,
            current,
            date.fromordinal(current.toordinal() + 1),
            f"Aaj ka hisaab | {_format_date(current)}",
        )
        return
    if parsed.query_type == "month":
        current = parsed.date or today_ist()
        start, end = month_bounds(current.year, current.month)
        await _send_period(
            message, session, shop_id, start, end, f"{start.strftime('%B %Y')} ka hisaab"
        )
        return
    if not parsed.customer:
        await message.reply_text("Kis customer ka hisaab chahiye? Naam likhiye.")
        return
    matches = customer_matches(session, shop_id, parsed.customer)
    if not matches:
        await message.reply_text(f"{parsed.customer} naam ka customer nahi mila.")
        return
    customer = matches[0]
    if matches[0].normalized_name != normalize_name(parsed.customer):
        buttons = [
            InlineKeyboardButton(
                item.name, callback_data=f"ledger:{item.id}"
            )
            for item in matches[:4]
        ]
        await message.reply_text(
            "Kaunsa customer ka khata chahiye?",
            reply_markup=InlineKeyboardMarkup([buttons]),
        )
        return
    await _show_customer_ledger(message, session, shop_id, customer)


async def _create_write_confirmation(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    membership: tuple[int, int, int],
    parsed: ParsedMessage,
) -> None:
    local_user_id, shop_id, telegram_user_id = membership
    if not parsed.customer:
        await message.reply_text("Customer ka naam batayein, tab main khata bana sakta hoon.")
        return
    kind = parsed.intent
    if kind == "entry":
        if not parsed.items:
            await message.reply_text("Kaunsa samaan diya aur uski total keemat kya thi?")
            return
        if any(item.price is None for item in parsed.items):
            item_names = ", ".join(item.name for item in parsed.items if item.price is None)
            await message.reply_text(f"{item_names} ki total keemat bataiye. Main andaza nahi lagaunga.")
            return
        items = [
            {
                "name": item.name.strip()[:160],
                "quantity": item.quantity,
                "price_paise": rupees_to_paise(item.price),
            }
            for item in parsed.items
        ]
        total_paise = sum(item["price_paise"] for item in items)
        if total_paise > context.application.bot_data["settings"].max_price_warning * 100:
            warning = True
        else:
            warning = False
    elif kind == "payment":
        if parsed.amount is None:
            await message.reply_text("Payment ki exact rakam batayein. Main andaza nahi lagaunga.")
            return
        items = []
        total_paise = rupees_to_paise(parsed.amount)
        warning = total_paise > context.application.bot_data["settings"].max_price_warning * 100
    else:
        return

    payload = {
        "kind": kind,
        "date": (parsed.date or today_ist()).isoformat(),
        "customer_name": parsed.customer.strip()[:120],
        "items": items,
        "amount_paise": total_paise if kind == "payment" else None,
        "new_customer": False,
    }
    session_factory = context.application.bot_data["session_factory"]
    with session_factory.begin() as session:
        matches = customer_matches(session, shop_id, parsed.customer)
        exact = [item for item in matches if item.normalized_name == normalize_name(parsed.customer)]
        if len(exact) == 1:
            payload["customer_id"] = exact[0].id
            payload["customer_name"] = exact[0].name
            candidates = []
        else:
            candidates = matches
            if candidates:
                payload["candidate_ids"] = [item.id for item in candidates]
                payload["new_customer"] = True
            else:
                payload["new_customer"] = True

        if candidates:
            confirmation = create_confirmation(
                session, shop_id, local_user_id, "choose_customer", payload
            )
            buttons = [
                InlineKeyboardButton(
                    candidate.name,
                    callback_data=f"pick:{confirmation.id}:{candidate.id}",
                )
                for candidate in candidates[:4]
            ]
            buttons.append(
                InlineKeyboardButton(
                    f"New customer: {parsed.customer[:30]}",
                    callback_data=f"new:{confirmation.id}",
                )
            )
            if any(len(button.callback_data.encode("utf-8")) > 64 for button in buttons):
                raise ValueError("Customer callback data exceeds Telegram's 64-byte limit.")
            keyboard = InlineKeyboardMarkup([buttons[:2], buttons[2:]])
            text = f"Is naam se milte-julte customers mile. Sahi customer chuniye:\n{', '.join(item.name for item in candidates[:4])}"
            await message.reply_text(text, reply_markup=keyboard)
            return

        confirmation = create_confirmation(
            session, shop_id, local_user_id, kind, payload
        )
        preview = _confirmation_text(payload)
        if warning:
            preview += "\n⚠️ Rakam zyada hai. Kripya dobara check karein."
        await message.reply_text(
            preview, reply_markup=_save_keyboard(confirmation.id)
        )


async def _start_customer_creation(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    membership: tuple[int, int, int],
    name: str,
) -> None:
    local_user_id, shop_id, telegram_user_id = membership
    clean_name = " ".join(name.strip().split())[:120]
    if not clean_name:
        await message.reply_text("Naye customer ka naam likhiye.")
        return
    session_factory = context.application.bot_data["session_factory"]
    with session_factory.begin() as session:
        matches = customer_matches(session, shop_id, clean_name)
        if any(item.normalized_name == normalize_name(clean_name) for item in matches):
            await message.reply_text(f"{clean_name} pehle se khate mein hai.")
            return
        if matches:
            confirmation = create_confirmation(
                session,
                shop_id,
                local_user_id,
                "customer_similar",
                {
                    "customer_name": clean_name,
                    "candidate_ids": [item.id for item in matches[:4]],
                },
            )
            buttons = [
                InlineKeyboardButton(
                    item.name,
                    callback_data=f"known:{confirmation.id}:{item.id}",
                )
                for item in matches[:4]
            ]
            buttons.append(
                InlineKeyboardButton(
                    f"Create {clean_name[:24]} anyway",
                    callback_data=f"createcustomer:{confirmation.id}",
                )
            )
            await message.reply_text(
                f"Milte-julte customer mile. Kya {clean_name} naya customer hai?",
                reply_markup=InlineKeyboardMarkup([buttons]),
            )
            return
        confirmation = create_confirmation(
            session,
            shop_id,
            local_user_id,
            "customer_create",
            {"customer_name": clean_name},
        )
    await message.reply_text(
        f"Naya customer khate mein add karein: {clean_name}?",
        reply_markup=_save_keyboard(confirmation.id),
    )


async def _start_clear_customer_ledger(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    membership: tuple[int, int, int],
    customer_name: str | None,
) -> None:
    local_user_id, shop_id, _telegram_user_id = membership
    if not customer_name:
        await message.reply_text(
            "Kis customer ka khata clear karna hai? Exact naam likhiye."
        )
        return
    session_factory = context.application.bot_data["session_factory"]
    normalized_name = normalize_name(customer_name)
    with session_factory.begin() as session:
        customer = session.scalar(
            select(Customer).where(
                Customer.shop_id == shop_id,
                Customer.normalized_name == normalized_name,
            )
        )
        if customer is None:
            matches = customer_matches(session, shop_id, customer_name)
            if matches:
                await message.reply_text(
                    "Galat customer ka ledger clear na ho, isliye exact naam "
                    "match nahi hua. Customer ka sahi naam dobara likhiye."
                )
            else:
                await message.reply_text(f"{customer_name} naam ka customer nahi mila.")
            return
        entries = session.scalar(
            select(func.count(Entry.id)).where(
                Entry.shop_id == shop_id,
                Entry.customer_id == customer.id,
                Entry.voided_at.is_(None),
            )
        ) or 0
        payments = session.scalar(
            select(func.count(Payment.id)).where(
                Payment.shop_id == shop_id,
                Payment.customer_id == customer.id,
                Payment.voided_at.is_(None),
            )
        ) or 0
        if entries + payments == 0:
            await message.reply_text(
                f"{customer.name} ka ledger pehle se khaali hai. Kuch delete nahi hua."
            )
            return
        confirmation = create_confirmation(
            session,
            shop_id,
            local_user_id,
            "clear_customer_ledger",
            {"customer_id": customer.id, "customer_name": customer.name},
        )
        confirmation_id = confirmation.id
        response = (
            f"{customer.name} ke poore ledger ke {entries} entries aur "
            f"{payments} payments clear karne hain? Customer record rahega; "
            "ledger rows audit ke liye soft-void hongi. Confirm kiye bina "
            "kuch nahi badlega."
        )
    await message.reply_text(
        response, reply_markup=_save_keyboard(confirmation_id)
    )


async def text_fallback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    user = update.effective_user
    if user is None:
        return
    membership = await _authorized(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return

    settings: Settings = context.application.bot_data["settings"]
    if not claim_update(
        context.application.bot_data["session_factory"], update.update_id
    ):
        return
    if _rate_limited(context, user.id, settings.rate_limit_per_minute):
        await message.reply_text("Thoda ruk kar phir bhejiye, sir.")
        return
    local_user_id, shop_id, _telegram_user_id = membership
    session_factory = context.application.bot_data["session_factory"]
    conversation = remember_user_message(
        session_factory, local_user_id, shop_id, message.text
    )
    if re.search(
        r"\b(?:jayega|jaega|kar\s*diya|kr\s*diya|ho\s*gaya|already|delete\s*hua)\b",
        message.text,
        flags=re.I,
    ):
        with session_factory() as session:
            pending_delete = session.scalar(
                select(PendingConfirmation)
                .where(
                    PendingConfirmation.shop_id == shop_id,
                    PendingConfirmation.user_id == local_user_id,
                    PendingConfirmation.kind == "clear_customer_ledger",
                    PendingConfirmation.status == "pending",
                    PendingConfirmation.expires_at > utc_now_naive(),
                )
                .order_by(PendingConfirmation.id.desc())
                .limit(1)
            )
        if pending_delete is not None:
            await message.reply_text(
                "Abhi nahi, sir. Khata clear nahi hua hai. Neeche diye gaye "
                "confirmation button ko dabane ke baad hi "
                f"{pending_delete.payload['customer_name']} ka ledger clear hoga."
            )
            return
    try:
        rule_parsed = parse_with_rules(
            message.text, last_customer=conversation.last_customer
        )
        if rule_parsed.intent == "delete_ledger":
            parsed, used_fallback = rule_parsed, True
        else:
            parsed, used_fallback = await parse_message(
                message.text,
                settings,
                conversation_history=conversation.history,
                last_customer=conversation.last_customer,
            )
        if used_fallback:
            logger.info("Rule-based parsing used for one shopkeeper message.")
    except (ValueError, SQLAlchemyError, httpx.HTTPError) as error:
        logger.error("Message processing failed (%s).", type(error).__name__)
        await message.reply_text("Maaf kijiye, is message ko samajh nahi paya. Thoda alag tarike se bhejiye.")
        return

    if parsed.customer and parsed.intent != "delete_ledger":
        remember_customer(session_factory, local_user_id, shop_id, parsed.customer)
    if parsed.intent == "delete_ledger":
        await _start_clear_customer_ledger(
            message, context, membership, parsed.customer
        )
        return
    if parsed.intent == "other":
        add_customer = re.match(
            r"^(?:please\s+)?(?:add|create|new|register)\s+"
            r"(?:a\s+)?(?:new\s+)?customer\s+(.+)$",
            message.text.strip(),
            flags=re.I,
        )
        if add_customer:
            await _start_customer_creation(
                message, context, membership, add_customer.group(1)
            )
            return
        reply = parsed.reply or "Hello sir! Aapke khate mein kaise madad karun?"
        if used_fallback:
            reply = f"⚠️ Internet/AI available nahi tha; simple reply de raha hoon.\n{reply}"
        await message.reply_text(reply)
        return

    if parsed.intent == "entry" and not parsed.customer and conversation.last_customer:
        parsed.customer = conversation.last_customer
    if parsed.intent == "query":
        if used_fallback:
            await message.reply_text(
                "⚠️ AI service available nahi thi; basic parser se hisaab samjha."
            )
        with session_factory() as session:
            await _reply_to_query(message, session, shop_id, parsed)
        return

    try:
        await _create_write_confirmation(message, context, membership, parsed)
    except (ValueError, SQLAlchemyError) as error:
        logger.error(
            "Could not create a pending ledger confirmation (%s).",
            type(error).__name__,
        )
        await message.reply_text("Khata save karne ke liye tayyar nahi ho saka. Kripya dobara bhejiye.")
        return
    if used_fallback:
        await message.reply_text(
            "⚠️ AI service available nahi thi, isliye basic parser se samjha hai. Save karne se pehle details check karein."
        )


async def confirmation_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    await query.answer()
    user = update.effective_user
    if user is None or query.message is None:
        return
    membership = await _authorized(update, context)
    if membership is None:
        await query.edit_message_text("Not authorized.")
        return
    local_user_id, shop_id, telegram_user_id = membership
    if not claim_update(
        context.application.bot_data["session_factory"], update.update_id
    ):
        return
    parts = query.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await query.edit_message_text("Yeh button ab kaam nahi karta. Message dobara bhejiye.")
        return
    action = parts[0]
    confirmation_id = int(parts[1])
    session_factory = context.application.bot_data["session_factory"]
    if action in {"pick", "new", "known", "createcustomer"}:
        candidate_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
        with session_factory.begin() as session:
            confirmation = get_pending_confirmation(
                session, confirmation_id, shop_id, local_user_id
            )
            if confirmation is None:
                await query.edit_message_text("Yeh confirmation ab active nahi hai.")
                return
            if expired(confirmation):
                confirmation.status = "expired"
                await query.edit_message_text("Yeh request expire ho gayi. Message phir se bhejiye.")
                return
            if confirmation.kind == "customer_similar":
                if action == "known" and candidate_id is not None:
                    if candidate_id not in confirmation.payload["candidate_ids"]:
                        await query.edit_message_text("Yeh customer choice active nahi hai.")
                        return
                    customer = session.scalar(
                        select(Customer).where(
                            Customer.id == candidate_id,
                            Customer.shop_id == shop_id,
                        )
                    )
                    if customer is None:
                        await query.edit_message_text("Customer nahi mila.")
                        return
                    rows = ledger_rows(session, shop_id, customer.id)
                    text, keyboard = _ledger_text(customer, rows, 0)
                    confirmation.status = "cancelled"
                else:
                    if action != "createcustomer":
                        await query.edit_message_text("Yeh customer choice active nahi hai.")
                        return
                    confirmation.kind = "customer_create"
                    confirmation.payload["explicit_new"] = True
                    text = (
                        f"Naya customer khate mein add karein: "
                        f"{confirmation.payload['customer_name']}?"
                    )
                    keyboard = _save_keyboard(confirmation.id)
                customer_similar_result = (text, keyboard)
            else:
                customer_similar_result = None
            if customer_similar_result is not None:
                pass
            elif confirmation.kind != "choose_customer":
                await query.edit_message_text("Yeh customer choice ab active nahi hai.")
                return
            if customer_similar_result is not None:
                pass
            elif action == "pick" and candidate_id is not None:
                customer = session.scalar(
                    select(Customer).where(
                        Customer.id == candidate_id,
                        Customer.shop_id == shop_id,
                    )
                )
                if customer is None:
                    await query.edit_message_text("Customer nahi mila. Message phir se bhejiye.")
                    return
                confirmation.payload["customer_id"] = customer.id
                confirmation.payload["customer_name"] = customer.name
                confirmation.payload["new_customer"] = False
            else:
                confirmation.payload.pop("candidate_ids", None)
                confirmation.payload.pop("customer_id", None)
                confirmation.payload["new_customer"] = True
                confirmation.payload["explicit_new"] = True
            if customer_similar_result is None:
                if action not in {"pick", "new"}:
                    await query.edit_message_text("Yeh customer choice ab active nahi hai.")
                    return
                confirmation.kind = confirmation.payload["kind"]
                text = _confirmation_text(confirmation.payload)
                keyboard = _save_keyboard(confirmation.id)
        if customer_similar_result is not None:
            await query.edit_message_text(
                customer_similar_result[0],
                reply_markup=customer_similar_result[1],
            )
            return
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    if action not in {"save", "cancel"}:
        if action == "deleteprepare":
            with session_factory.begin() as session:
                confirmation = get_pending_confirmation(
                    session, confirmation_id, shop_id, local_user_id
                )
                if confirmation is None or confirmation.kind != "delete_stage1":
                    await query.edit_message_text("Yeh export confirmation ab active nahi hai.")
                    return
                if expired(confirmation):
                    confirmation.status = "expired"
                    await query.edit_message_text("Yeh request expire ho gayi. /deletemydata dobara bhejiye.")
                    return
                confirmation.kind = "delete_stage2"
            await query.edit_message_text(
                "Aakhri confirmation: is shop ka poora khata permanently delete hoga. "
                "Export pehle bhej diya gaya hai. Kya aap waqai delete karna chahte hain?",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            "Delete all shop data",
                            callback_data=f"delconfirm:{confirmation_id}",
                        ),
                        InlineKeyboardButton(
                            "Cancel", callback_data=f"cancel:{confirmation_id}"
                        ),
                    ]]
                ),
            )
            return
        if action == "delconfirm":
            with session_factory.begin() as session:
                deleted = delete_exported_shop_data(
                    session, shop_id, confirmation_id, local_user_id
                )
            await query.edit_message_text(
                "Shop ka data delete kar diya gaya hai."
                if deleted
                else "Yeh delete confirmation ab active nahi hai."
            )
            return
        if action == "edit":
            with session_factory.begin() as session:
                entry = session.scalar(
                    select(Entry).where(
                        Entry.id == confirmation_id,
                        Entry.shop_id == shop_id,
                        Entry.voided_at.is_(None),
                    )
                )
                if entry is None:
                    await query.edit_message_text(
                        "Yeh entry nahi mili ya pehle hi void hai."
                    )
                    return
                confirmation = create_confirmation(
                    session,
                    shop_id,
                    local_user_id,
                    "edit_menu",
                    {"entry_id": entry.id},
                )
                buttons = [
                    InlineKeyboardButton(
                        label,
                        callback_data=f"field:{confirmation.id}:{field}",
                    )
                    for label, field in (
                        ("Price", "price"),
                        ("Item", "item"),
                        ("Date", "date"),
                    )
                ]
                buttons.append(
                    InlineKeyboardButton(
                        "Delete entry",
                        callback_data=f"delete_entry:{entry.id}",
                    )
                )
                if any(
                    len(button.callback_data.encode("utf-8")) > 64
                    for button in buttons
                ):
                    raise ValueError(
                        "Edit callback data exceeds Telegram's 64-byte limit."
                    )
                text = (
                    f"{entry.item_name} {entry.quantity_text or ''} "
                    f"{format_paise(entry.price_paise)} — kya badalna hai?"
                )
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup([buttons])
            )
            return
        if action == "field" and len(parts) == 3 and parts[2] in {"price", "item", "date"}:
            with session_factory.begin() as session:
                confirmation = get_pending_confirmation(
                    session, confirmation_id, shop_id, local_user_id
                )
                if confirmation is None or confirmation.kind != "edit_menu":
                    await query.edit_message_text("Yeh edit request ab active nahi hai.")
                    return
                confirmation.kind = "edit_value"
                confirmation.status = "awaiting_input"
                confirmation.payload["field"] = parts[2]
            await query.edit_message_text(
                f"Nayi {parts[2]} bhejne ke liye yeh command bhejiye:\n"
                f"/editvalue {confirmation_id} <new value>"
            )
            return
        if action == "delete_entry":
            with session_factory.begin() as session:
                entry = session.scalar(
                    select(Entry).where(
                        Entry.id == confirmation_id,
                        Entry.shop_id == shop_id,
                        Entry.voided_at.is_(None),
                    )
                )
                if entry is None:
                    await query.edit_message_text(
                        "Yeh entry nahi mili ya pehle hi void hai."
                    )
                    return
                customer = session.scalar(
                    select(Customer).where(
                        Customer.id == entry.customer_id,
                        Customer.shop_id == shop_id,
                    )
                )
                confirmation = create_confirmation(
                    session,
                    shop_id,
                    local_user_id,
                    "delete_entry",
                    {"entry_id": entry.id},
                )
                text = (
                    f"{customer.name if customer else 'Customer'} — "
                    f"{entry.item_name} {format_paise(entry.price_paise)} "
                    "ko void karna hai?"
                )
            await query.edit_message_text(
                text, reply_markup=_save_keyboard(confirmation.id)
            )
            return
        if action == "undo":
            with session_factory.begin() as session:
                confirmation = claim_pending_confirmation(
                    session, confirmation_id, shop_id, local_user_id
                )
                if confirmation is None or confirmation.kind != "undo":
                    await query.edit_message_text("Yeh undo request ab active nahi hai.")
                    return
                if expired(confirmation):
                    confirmation.status = "expired"
                    await query.edit_message_text(
                        "Yeh request expire ho gayi. Dobara /undo bhejiye."
                    )
                    return
                details = confirmation.payload
                if details["record_type"] == "entry":
                    record = session.scalar(
                        select(Entry).where(
                            Entry.id == details["record_id"],
                            Entry.shop_id == shop_id,
                            Entry.voided_at.is_(None),
                        )
                    )
                else:
                    record = session.scalar(
                        select(Payment).where(
                            Payment.id == details["record_id"],
                            Payment.shop_id == shop_id,
                            Payment.voided_at.is_(None),
                        )
                    )
                if record is None:
                    confirmation.status = "cancelled"
                    result_text = "Yeh record pehle hi badal chuka hai."
                else:
                    record.voided_at = utc_now_naive()
                    confirmation.status = "done"
                    audit(
                        session,
                        shop_id,
                        telegram_user_id,
                        "record_undone",
                        {
                            "record_type": details["record_type"],
                            "record_id": details["record_id"],
                        },
                    )
                    balance = customer_balance(session, shop_id, record.customer_id)
                    result_text = (
                        f"✅ Record undo ho gaya. Naya balance: {format_paise(balance)}"
                    )
            await query.edit_message_text(result_text)
            return
        if action == "page" and len(parts) == 3 and parts[2].isdigit():
            customer_id = confirmation_id
            page = int(parts[2])
            with session_factory() as session:
                customer = session.scalar(
                    select(Customer).where(
                        Customer.id == customer_id, Customer.shop_id == shop_id
                    )
                )
                if customer is None:
                    await query.edit_message_text("Customer ka khata nahi mila.")
                    return
                rows = ledger_rows(session, shop_id, customer.id)
                text, keyboard = _ledger_text(customer, rows, page)
            await query.edit_message_text(text, reply_markup=keyboard)
            return
        if action == "ledger":
            with session_factory() as session:
                customer = session.scalar(
                    select(Customer).where(
                        Customer.id == confirmation_id,
                        Customer.shop_id == shop_id,
                    )
                )
                if customer is None:
                    await query.edit_message_text("Customer ka khata nahi mila.")
                    return
                rows = ledger_rows(session, shop_id, customer.id)
                text, keyboard = _ledger_text(customer, rows, 0)
            await query.edit_message_text(text, reply_markup=keyboard)
            return
        await query.edit_message_text("Yeh button ab kaam nahi karta.")
        return

    with session_factory.begin() as session:
        confirmation = (
            claim_pending_confirmation(
                session, confirmation_id, shop_id, local_user_id
            )
            if action == "save"
            else get_pending_confirmation(
                session, confirmation_id, shop_id, local_user_id
            )
        )
        if confirmation is None:
            await query.edit_message_text("Yeh request pehle hi process ho chuki hai.")
            return
        if expired(confirmation):
            confirmation.status = "expired"
            await query.edit_message_text("Yeh request expire ho gayi. Message phir se bhejiye.")
            return
        if action == "cancel":
            confirmation.status = "cancelled"
            result_text = "❌ Cancel kar diya. Khate mein kuch nahi badla."
        elif confirmation.kind in {"delete_entry", "edit_change"}:
            payload = confirmation.payload
            entry = session.scalar(
                select(Entry).where(
                    Entry.id == payload["entry_id"],
                    Entry.shop_id == shop_id,
                    Entry.voided_at.is_(None),
                )
            )
            if entry is None:
                confirmation.status = "cancelled"
                result_text = "Yeh entry nahi mili ya pehle hi badal chuki hai."
            elif confirmation.kind == "delete_entry":
                entry.voided_at = utc_now_naive()
                confirmation.status = "done"
                audit(
                    session,
                    shop_id,
                    telegram_user_id,
                    "entry_voided",
                    {"entry_id": entry.id},
                )
                result_text = "✅ Entry void kar di. Audit record rakha gaya hai."
            else:
                field = payload["field"]
                if field == "price":
                    entry.price_paise = payload["new_value"]
                elif field == "date":
                    entry.entry_date = date.fromisoformat(payload["new_value"])
                else:
                    entry.item_name = payload["new_value"]
                confirmation.status = "done"
                audit(
                    session,
                    shop_id,
                    telegram_user_id,
                    "entry_edited",
                    {"entry_id": entry.id, "field": field},
                )
                result_text = "✅ Entry update ho gayi."
        elif confirmation.kind == "customer_create":
            customer_name = confirmation.payload["customer_name"]
            similar = customer_matches(session, shop_id, customer_name)
            if similar and not confirmation.payload.get("explicit_new"):
                confirmation.status = "cancelled"
                result_text = (
                    "Naya customer create nahi kiya, kyunki milta-julta naam "
                    "pehle se khate mein hai. Naam check karke dobara bhejiye."
                )
            else:
                customer = Customer(
                    shop_id=shop_id,
                    name=customer_name,
                    normalized_name=normalize_name(customer_name),
                )
                session.add(customer)
                session.flush()
                confirmation.status = "done"
                audit(
                    session,
                    shop_id,
                    telegram_user_id,
                    "customer_created",
                    {"customer_id": customer.id},
                )
                result_text = f"✅ {customer.name} customer khate mein add ho gaya."
        elif confirmation.kind == "clear_customer_ledger":
            customer = session.scalar(
                select(Customer).where(
                    Customer.id == confirmation.payload["customer_id"],
                    Customer.shop_id == shop_id,
                )
            )
            if customer is None:
                confirmation.status = "cancelled"
                result_text = "Customer nahi mila; ledger mein kuch badla nahi."
            else:
                entries_voided, payments_voided = void_customer_ledger(
                    session, shop_id, customer.id, telegram_user_id
                )
                confirmation.status = "done"
                result_text = (
                    f"✅ {customer.name} ka ledger clear kar diya. "
                    f"{entries_voided} entries aur {payments_voided} payments "
                    "audit ke liye rakhe gaye hain."
                )
        else:
            payload = confirmation.payload
            customer = None
            if payload.get("customer_id"):
                customer = session.scalar(
                    select(Customer).where(
                        Customer.id == payload["customer_id"],
                        Customer.shop_id == shop_id,
                    )
                )
            elif payload.get("new_customer"):
                matches = customer_matches(session, shop_id, payload["customer_name"])
                if matches:
                    confirmation.status = "pending"
                    await query.edit_message_text(
                        "Is naam ka milta-julta customer abhi bana. Customer ko chuniye, phir save karein."
                    )
                    return
                customer = Customer(
                    shop_id=shop_id,
                    name=payload["customer_name"],
                    normalized_name=normalize_name(payload["customer_name"]),
                )
                session.add(customer)
                session.flush()
            if customer is None:
                confirmation.status = "cancelled"
                result_text = "Customer nahi mila; kuch save nahi hua. Message dobara bhejiye."
            elif confirmation.kind == "entry":
                balance = record_entry(
                    session,
                    shop_id,
                    customer.id,
                    date.fromisoformat(payload["date"]),
                    payload["items"],
                    telegram_user_id,
                )
                confirmation.status = "done"
                audit(
                    session,
                    shop_id,
                    telegram_user_id,
                    "entry_created",
                    {"confirmation_id": confirmation.id, "customer_id": customer.id},
                )
                result_text = f"✅ Saved. {customer.name} ka naya balance: {format_paise(balance)}"
            elif confirmation.kind == "payment":
                balance = record_payment(
                    session,
                    shop_id,
                    customer.id,
                    date.fromisoformat(payload["date"]),
                    payload["amount_paise"],
                    telegram_user_id,
                )
                confirmation.status = "done"
                audit(
                    session,
                    shop_id,
                    telegram_user_id,
                    "payment_created",
                    {"confirmation_id": confirmation.id, "customer_id": customer.id},
                )
                result_text = f"✅ Payment saved. {customer.name} ka naya balance: {format_paise(balance)}"
            else:
                confirmation.status = "cancelled"
                result_text = "Is request ko save nahi kar sakte."
    await query.edit_message_text(result_text)


async def command_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    membership = await _authorized(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not claim_update(
        context.application.bot_data["session_factory"], update.update_id
    ):
        return
    local_user_id, shop_id, _telegram_id = membership
    command = (message.text or "").split()[0].split("@")[0].lower()
    args = context.args
    parsed = ParsedMessage(intent="query", date=today_ist())
    if command == "/all":
        parsed.query_type = "all"
    elif command == "/today":
        parsed.query_type = "today"
    elif command == "/month":
        parsed.query_type = "month"
        if args:
            from khata_bot.services.dates import parse_date

            parsed.date = parse_date("1 " + " ".join(args)) or parsed.date
    else:
        parsed.query_type = "customer_ledger"
        if not args:
            await message.reply_text("Use: /ledger <customer name>")
            return
        parsed.customer = " ".join(args)
    session_factory = context.application.bot_data["session_factory"]
    with session_factory() as session:
        await _reply_to_query(message, session, shop_id, parsed)


async def unknown_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    membership = await _authorized(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not claim_update(
        context.application.bot_data["session_factory"], update.update_id
    ):
        return
    await message.reply_text("Yeh command nahi samajh aaya. Madad ke liye /start bhejiye.")


async def forget_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    membership = await _authorized(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not claim_update(
        context.application.bot_data["session_factory"], update.update_id
    ):
        return
    user_id, _shop_id, _telegram_user_id = membership
    clear_conversation_memory(
        context.application.bot_data["session_factory"], user_id
    )
    await message.reply_text(
        "Theek hai, aapki recent chat memory clear kar di. Khate ke records par koi asar nahi hua."
    )


async def customer_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    membership = await _authorized(update, context)
    if membership is None:
        await message.reply_text("Not authorized.")
        return
    if not claim_update(
        context.application.bot_data["session_factory"], update.update_id
    ):
        return
    if not context.args:
        await message.reply_text("Use: /customer <new customer name>")
        return
    await _start_customer_creation(
        message, context, membership, " ".join(context.args)
    )
