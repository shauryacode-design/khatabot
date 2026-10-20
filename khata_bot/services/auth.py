"""Shop membership lookup for Telegram users."""

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from khata_bot.models import User


def find_authorized_user(session: Session, telegram_user_id: int) -> User | None:
    """Return the user's membership and shop, or None if the user is unregistered."""
    statement = (
        select(User)
        .options(joinedload(User.shop))
        .where(User.telegram_user_id == telegram_user_id)
    )
    return session.scalar(statement)
