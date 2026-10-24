"""Explicit, transactional removal of an exported shop's data."""

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from khata_bot.models import AuditLog, Customer, Entry, Invite, Payment, PendingConfirmation, Shop, User
from khata_bot.services.ledger import utc_now


def delete_exported_shop_data(
    session: Session,
    shop_id: int,
    confirmation_id: int,
    user_id: int,
) -> bool:
    """Delete one shop only after its owner reaches the second confirmation."""
    result = session.execute(
        update(PendingConfirmation)
        .where(
            PendingConfirmation.id == confirmation_id,
            PendingConfirmation.shop_id == shop_id,
            PendingConfirmation.user_id == user_id,
            PendingConfirmation.kind == "delete_stage2",
            PendingConfirmation.status == "pending",
            PendingConfirmation.expires_at > utc_now(),
        )
        .values(status="processing")
    )
    if result.rowcount != 1:
        return False
    session.execute(delete(Entry).where(Entry.shop_id == shop_id))
    session.execute(delete(Payment).where(Payment.shop_id == shop_id))
    session.execute(delete(AuditLog).where(AuditLog.shop_id == shop_id))
    session.execute(delete(Invite).where(Invite.shop_id == shop_id))
    session.execute(delete(PendingConfirmation).where(PendingConfirmation.shop_id == shop_id))
    session.execute(delete(Customer).where(Customer.shop_id == shop_id))
    session.execute(delete(User).where(User.shop_id == shop_id))
    session.execute(delete(Shop).where(Shop.id == shop_id))
    return True
