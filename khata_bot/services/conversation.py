"""Short, per-user conversation context for resolving follow-up messages."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from khata_bot.models import ConversationMemory

_MAX_MESSAGES = 8
_MAX_MESSAGE_LENGTH = 500


@dataclass(frozen=True)
class ConversationContext:
    history: list[str]
    last_customer: str | None


def remember_user_message(
    session_factory: sessionmaker,
    user_id: int,
    shop_id: int,
    message: str,
) -> ConversationContext:
    clean_message = message.strip()[:_MAX_MESSAGE_LENGTH]
    with session_factory.begin() as session:
        memory = session.scalar(
            select(ConversationMemory)
            .where(
                ConversationMemory.user_id == user_id,
                ConversationMemory.shop_id == shop_id,
            )
            .with_for_update()
        )
        if memory is None:
            memory = ConversationMemory(
                user_id=user_id,
                shop_id=shop_id,
                history=[],
            )
            session.add(memory)
            session.flush()
        context = ConversationContext(
            history=[
                item["content"]
                for item in memory.history
                if item.get("role") == "user" and isinstance(item.get("content"), str)
            ],
            last_customer=memory.last_customer_name,
        )
        memory.history = [
            *memory.history,
            {"role": "user", "content": clean_message},
        ][-_MAX_MESSAGES:]
        return context


def remember_customer(
    session_factory: sessionmaker,
    user_id: int,
    shop_id: int,
    customer_name: str,
) -> None:
    clean_name = " ".join(customer_name.strip().split())[:120]
    if not clean_name:
        return
    with session_factory.begin() as session:
        memory = session.scalar(
            select(ConversationMemory).where(
                ConversationMemory.user_id == user_id,
                ConversationMemory.shop_id == shop_id,
            )
        )
        if memory is not None:
            memory.last_customer_name = clean_name


def clear_conversation_memory(session_factory: sessionmaker, user_id: int) -> None:
    with session_factory.begin() as session:
        memory = session.get(ConversationMemory, user_id)
        if memory is not None:
            session.delete(memory)
