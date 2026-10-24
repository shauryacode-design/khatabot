"""Claim Telegram updates once so redelivery cannot repeat side effects."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from khata_bot.models import ProcessedUpdate


def claim_update(session_factory: sessionmaker, update_id: int) -> bool:
    try:
        with session_factory.begin() as session:
            session.add(ProcessedUpdate(update_id=update_id))
            session.flush()
    except IntegrityError:
        return False
    return True
