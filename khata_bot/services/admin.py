"""Shop administration and single-use invite management."""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from khata_bot.models import Invite, Shop, User
from khata_bot.services.ledger import utc_now


def create_shop(session: Session, name: str, admin_telegram_id: int) -> Shop:
    clean_name = " ".join(name.split())
    if not clean_name:
        raise ValueError("Shop name is required.")
    shop = Shop(name=clean_name[:120])
    session.add(shop)
    session.flush()
    existing_admin = session.scalar(
        select(User).where(User.telegram_user_id == admin_telegram_id)
    )
    if existing_admin is None:
        session.add(
            User(
                telegram_user_id=admin_telegram_id,
                shop_id=shop.id,
                role="owner",
            )
        )
    return shop


def add_user(session: Session, shop_id: int, telegram_user_id: int) -> User:
    shop = session.scalar(select(Shop).where(Shop.id == shop_id))
    if shop is None:
        raise ValueError("Shop not found.")
    existing = session.scalar(
        select(User).where(User.telegram_user_id == telegram_user_id)
    )
    if existing is not None:
        if existing.shop_id == shop_id:
            return existing
        raise ValueError("This Telegram user is already registered to another shop.")
    user = User(
        telegram_user_id=telegram_user_id,
        shop_id=shop_id,
        role="owner",
    )
    session.add(user)
    session.flush()
    return user


def create_invite(
    session: Session, shop_id: int, created_by: int
) -> str:
    if session.scalar(select(Shop.id).where(Shop.id == shop_id)) is None:
        raise ValueError("Shop not found.")
    code = secrets.token_urlsafe(24)
    session.add(
        Invite(
            shop_id=shop_id,
            token_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
            expires_at=utc_now() + timedelta(days=7),
            created_by=created_by,
        )
    )
    return code


def redeem_invite(
    session: Session, code: str, telegram_user_id: int
) -> User | None:
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    invite = session.scalar(
        select(Invite)
        .where(
            Invite.token_hash == code_hash,
            Invite.used_at.is_(None),
            Invite.expires_at > utc_now(),
        )
        .with_for_update()
    )
    if invite is None:
        return None
    existing = session.scalar(
        select(User).where(User.telegram_user_id == telegram_user_id)
    )
    if existing is not None:
        return None
    user = User(
        telegram_user_id=telegram_user_id,
        shop_id=invite.shop_id,
        role="owner",
    )
    invite.used_at = utc_now()
    session.add(user)
    session.flush()
    return user
