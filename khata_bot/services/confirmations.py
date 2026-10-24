"""Durable, shop-scoped Save/Cancel confirmations."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update

from khata_bot.models import PendingConfirmation
from khata_bot.services.ledger import utc_now_naive

CONFIRMATION_MINUTES = 30


def create_confirmation(
    session,
    shop_id: int,
    user_id: int,
    kind: str,
    payload: dict[str, Any],
) -> PendingConfirmation:
    confirmation = PendingConfirmation(
        shop_id=shop_id,
        user_id=user_id,
        kind=kind,
        payload=payload,
        created_at=utc_now_naive(),
        expires_at=utc_now_naive() + timedelta(minutes=CONFIRMATION_MINUTES),
        status="pending",
    )
    session.add(confirmation)
    session.flush()
    return confirmation


def get_pending_confirmation(
    session,
    confirmation_id: int,
    shop_id: int,
    user_id: int,
) -> PendingConfirmation | None:
    return session.scalar(
        select(PendingConfirmation)
        .where(
            PendingConfirmation.id == confirmation_id,
            PendingConfirmation.shop_id == shop_id,
            PendingConfirmation.user_id == user_id,
            PendingConfirmation.status == "pending",
        )
        .with_for_update()
    )


def claim_pending_confirmation(
    session,
    confirmation_id: int,
    shop_id: int,
    user_id: int,
) -> PendingConfirmation | None:
    """Atomically reserve a confirmation so concurrent taps cannot double-save."""
    result = session.execute(
        update(PendingConfirmation)
        .where(
            PendingConfirmation.id == confirmation_id,
            PendingConfirmation.shop_id == shop_id,
            PendingConfirmation.user_id == user_id,
            PendingConfirmation.status == "pending",
        )
        .values(status="processing")
    )
    if result.rowcount != 1:
        return None
    return session.scalar(
        select(PendingConfirmation).where(
            PendingConfirmation.id == confirmation_id,
            PendingConfirmation.shop_id == shop_id,
            PendingConfirmation.user_id == user_id,
            PendingConfirmation.status == "processing",
        )
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def expired(confirmation: PendingConfirmation) -> bool:
    return _as_utc(confirmation.expires_at) <= datetime.now(timezone.utc)
