import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from khata_bot.handlers.text import PHASE_ONE_REPLY, text_fallback
from khata_bot.models import Base, Shop, User


def _context(session_factory: sessionmaker) -> SimpleNamespace:
    application = SimpleNamespace(bot_data={"session_factory": session_factory})
    return SimpleNamespace(application=application)


def test_text_fallback_replies_to_authorized_user() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with Session(engine) as session:
        shop = Shop(name="Test Shop")
        session.add(shop)
        session.flush()
        session.add(User(telegram_user_id=111, shop_id=shop.id))
        session.commit()

    message = SimpleNamespace(text="sabka hisab", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message, effective_user=SimpleNamespace(id=111)
    )
    asyncio.run(text_fallback(update, _context(session_factory)))

    message.reply_text.assert_awaited_once_with(PHASE_ONE_REPLY)
    engine.dispose()


def test_text_fallback_denies_unregistered_user() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    message = SimpleNamespace(text="hello", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message, effective_user=SimpleNamespace(id=222)
    )
    asyncio.run(text_fallback(update, _context(session_factory)))

    message.reply_text.assert_awaited_once_with("Not authorized.")
    engine.dispose()
