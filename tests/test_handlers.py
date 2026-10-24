import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from cryptography.fernet import Fernet

from datetime import date

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from khata_bot.config import Settings
from khata_bot.handlers.text import (
    _reply_to_query,
    confirmation_callback,
    text_fallback,
)
from khata_bot.models import Base, Customer, Entry, PendingConfirmation, Shop, User
from khata_bot.parsing.schemas import ParsedItem, ParsedMessage
from khata_bot.services.conversation import remember_user_message, remember_customer


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ADMIN_TELEGRAM_ID": "111",
            "GROQ_API_KEY": "test-key",
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        }
    )


def _context(session_factory: sessionmaker) -> SimpleNamespace:
    application = SimpleNamespace(
        bot_data={"session_factory": session_factory, "settings": _settings()}
    )
    return SimpleNamespace(application=application)


def _database() -> tuple[object, sessionmaker, int]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with Session(engine) as session:
        shop = Shop(name="Test Shop")
        session.add(shop)
        session.flush()
        session.add(User(telegram_user_id=111, shop_id=shop.id))
        customer = Customer(
            shop_id=shop.id, name="Aman", normalized_name="aman"
        )
        session.add(customer)
        session.commit()
        shop_id = shop.id
    return engine, session_factory, shop_id


def test_text_fallback_replies_to_authorized_user(monkeypatch) -> None:
    engine, session_factory, _shop_id = _database()
    monkeypatch.setattr(
        "khata_bot.handlers.text.parse_message",
        AsyncMock(
            return_value=(
                ParsedMessage(intent="other", reply="Hello sir! Kaise madad karun?"),
                False,
            )
        ),
    )
    message = SimpleNamespace(text="sabka hisab", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=111),
        update_id=10,
    )
    asyncio.run(text_fallback(update, _context(session_factory)))

    message.reply_text.assert_awaited_once_with("Hello sir! Kaise madad karun?")
    engine.dispose()


def test_text_fallback_denies_unregistered_user() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    message = SimpleNamespace(text="hello", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=222),
        update_id=11,
    )
    asyncio.run(text_fallback(update, _context(session_factory)))

    message.reply_text.assert_awaited_once_with("Not authorized.")
    engine.dispose()


def test_query_asks_before_using_a_fuzzy_customer_match() -> None:
    engine, session_factory, shop_id = _database()
    message = SimpleNamespace(reply_text=AsyncMock())
    with Session(engine) as session:
        asyncio.run(
            _reply_to_query(
                message,
                session,
                shop_id,
                ParsedMessage(intent="query", customer="Amaan"),
            )
        )

    message.reply_text.assert_awaited_once()
    assert message.reply_text.await_args.args[0] == "Kaunsa customer ka khata chahiye?"
    assert message.reply_text.await_args.kwargs["reply_markup"] is not None
    engine.dispose()


def test_delete_request_never_claims_success_before_confirmation(monkeypatch) -> None:
    engine, session_factory, shop_id = _database()
    with Session(engine) as session:
        customer = Customer(
            shop_id=shop_id, name="Avinash", normalized_name="avinash"
        )
        session.add(customer)
        session.flush()
        session.add(
            Entry(
                shop_id=shop_id,
                customer_id=customer.id,
                entry_date=date(2026, 10, 20),
                item_name="aata",
                price_paise=36_000,
                created_by=111,
            )
        )
        session.commit()
    monkeypatch.setattr(
        "khata_bot.handlers.text.parse_message",
        AsyncMock(
            return_value=(
                ParsedMessage(intent="other", reply="Sure, deleted."),
                False,
            )
        ),
    )
    message = SimpleNamespace(
        text="Avinash ka hisab delete krdo", reply_text=AsyncMock()
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=111),
        update_id=18,
    )
    asyncio.run(text_fallback(update, _context(session_factory)))

    message.reply_text.assert_awaited_once()
    prompt = message.reply_text.await_args.args[0]
    assert "Avinash" in prompt
    assert "Confirm kiye bina kuch nahi badlega" in prompt
    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    assert any(
        button.callback_data.startswith("save:")
        for row in keyboard.inline_keyboard
        for button in row
    )
    with session_factory() as session:
        assert session.scalar(select(Entry.voided_at)) is None
        confirmation = session.scalar(select(PendingConfirmation))
        assert confirmation is not None
        assert confirmation.kind == "clear_customer_ledger"
        confirmation_id = confirmation.id

    status_message = SimpleNamespace(
        text="jayega ya krdiya?", reply_text=AsyncMock()
    )
    status_update = SimpleNamespace(
        effective_message=status_message,
        effective_user=SimpleNamespace(id=111),
        update_id=19,
    )
    asyncio.run(text_fallback(status_update, _context(session_factory)))
    status_message.reply_text.assert_awaited_once()
    assert "Abhi nahi" in status_message.reply_text.await_args.args[0]

    callback_query = SimpleNamespace(
        data=f"save:{confirmation_id}",
        message=SimpleNamespace(),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    callback_update = SimpleNamespace(
        callback_query=callback_query,
        effective_user=SimpleNamespace(id=111),
        update_id=20,
    )
    asyncio.run(confirmation_callback(callback_update, _context(session_factory)))
    callback_query.edit_message_text.assert_awaited_once()
    assert "1 entries aur 0 payments" in callback_query.edit_message_text.await_args.args[0]
    with session_factory() as session:
        assert session.scalar(select(Entry.voided_at)) is not None
        assert session.scalar(select(PendingConfirmation.status)) == "done"
    engine.dispose()


def test_conversation_memory_survives_between_messages() -> None:
    engine, session_factory, shop_id = _database()
    with Session(engine) as session:
        user_id = session.scalar(select(User.id).where(User.telegram_user_id == 111))

    initial = remember_user_message(
        session_factory, user_id, shop_id, "Katyani 300 chawal"
    )
    assert initial.history == []
    remember_customer(session_factory, user_id, shop_id, "Katyani")
    follow_up = remember_user_message(
        session_factory, user_id, shop_id, "chawal 300\ndaal 200"
    )

    assert follow_up.history == ["Katyani 300 chawal"]
    assert follow_up.last_customer == "Katyani"
    engine.dispose()


def test_entry_requires_confirmation_and_callback_is_idempotent(monkeypatch) -> None:
    engine, session_factory, shop_id = _database()
    monkeypatch.setattr(
        "khata_bot.handlers.text.parse_message",
        AsyncMock(
            return_value=(
                ParsedMessage(
                    intent="entry",
                    customer="Aman",
                    date=date(2026, 10, 20),
                    items=[ParsedItem(name="aata", quantity="10 kg", price=250)],
                ),
                False,
            )
        ),
    )
    message = SimpleNamespace(text="Aman aata 10kg 250", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=111),
        update_id=12,
    )
    asyncio.run(text_fallback(update, _context(session_factory)))

    message.reply_text.assert_awaited_once()
    preview = message.reply_text.await_args.args[0]
    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    assert "Aman" in preview
    assert "₹250" in preview
    callback_data = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert all(len(value.encode("utf-8")) <= 64 for value in callback_data)
    assert any(value.startswith("save:") for value in callback_data)
    with session_factory() as session:
        pending = session.scalar(select(PendingConfirmation))
        assert pending is not None
        confirmation_id = pending.id
        assert session.scalar(select(func.count(Entry.id))) == 0

    callback_message = SimpleNamespace(edit_text=AsyncMock())
    callback_query = SimpleNamespace(
        data=f"save:{confirmation_id}",
        message=callback_message,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    callback_update = SimpleNamespace(
        callback_query=callback_query,
        effective_user=SimpleNamespace(id=111),
        update_id=13,
    )
    context = _context(session_factory)
    asyncio.run(confirmation_callback(callback_update, context))
    callback_update.update_id = 14
    asyncio.run(confirmation_callback(callback_update, context))
    asyncio.run(confirmation_callback(callback_update, context))

    with session_factory() as session:
        assert session.scalar(select(func.count(Entry.id))) == 1
        assert session.scalar(select(PendingConfirmation.status)) == "done"
        assert session.scalar(select(Customer).where(Customer.shop_id == shop_id))
    assert callback_query.edit_message_text.await_count == 2
    engine.dispose()


def test_opening_balances_import_as_one_confirmed_batch() -> None:
    engine, session_factory, shop_id = _database()
    message = SimpleNamespace(
        text=(
            "Aman pending balance 5000\n"
            "Akash pending balance 2000\n"
            "Avinash pending balance 20000"
        ),
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=111),
        update_id=30,
    )
    context = _context(session_factory)
    asyncio.run(text_fallback(update, context))

    preview = message.reply_text.await_args.args[0]
    assert "Aman: ₹5,000" in preview
    assert "Akash: ₹2,000" in preview
    assert "Avinash: ₹20,000" in preview
    assert message.reply_text.await_args.kwargs["reply_markup"] is not None
    with session_factory() as session:
        confirmation = session.scalar(select(PendingConfirmation))
        assert confirmation is not None
        assert confirmation.kind == "opening_balances"
        assert session.scalar(select(func.count(Entry.id))) == 0
        confirmation_id = confirmation.id

    callback_query = SimpleNamespace(
        data=f"save:{confirmation_id}",
        message=SimpleNamespace(),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    callback_update = SimpleNamespace(
        callback_query=callback_query,
        effective_user=SimpleNamespace(id=111),
        update_id=31,
    )
    asyncio.run(confirmation_callback(callback_update, context))

    callback_query.edit_message_text.assert_awaited_once()
    assert "Initial balances saved" in callback_query.edit_message_text.await_args.args[0]
    with session_factory() as session:
        entries = session.scalars(
            select(Entry).where(Entry.shop_id == shop_id)
        ).all()
        assert len(entries) == 3
        assert {entry.item_name for entry in entries} == {"Opening balance"}
        balances = {
            customer.name: sum(
                entry.price_paise
                for entry in entries
                if entry.customer_id == customer.id
            )
            for customer in session.scalars(
                select(Customer).where(Customer.shop_id == shop_id)
            ).all()
        }
        assert balances == {
            "Aman": 500_000,
            "Akash": 200_000,
            "Avinash": 2_000_000,
        }
        assert session.scalar(select(PendingConfirmation.status)) == "done"
    engine.dispose()


def test_opening_balance_import_rejects_fuzzy_customer_match() -> None:
    engine, session_factory, _shop_id = _database()
    message = SimpleNamespace(
        text="Amaan pending balance 500",
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=111),
        update_id=32,
    )
    asyncio.run(text_fallback(update, _context(session_factory)))

    message.reply_text.assert_awaited_once()
    assert "milta-julta customer" in message.reply_text.await_args.args[0]
    with session_factory() as session:
        assert session.scalar(select(PendingConfirmation)) is None
        assert session.scalar(select(func.count(Entry.id))) == 0
    engine.dispose()
