"""Shop-scoped ledger reads and transactional writes."""

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from khata_bot.models import AuditLog, Customer, Entry, Payment

OPENING_BALANCE_ITEM = "Opening balance"


def customer_balance(session: Session, shop_id: int, customer_id: int) -> int:
    entry_total = session.scalar(
        select(func.coalesce(func.sum(Entry.price_paise), 0)).where(
            Entry.shop_id == shop_id,
            Entry.customer_id == customer_id,
            Entry.voided_at.is_(None),
        )
    )
    payment_total = session.scalar(
        select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
            Payment.shop_id == shop_id,
            Payment.customer_id == customer_id,
            Payment.voided_at.is_(None),
        )
    )
    return int(entry_total or 0) - int(payment_total or 0)


def all_balances(session: Session, shop_id: int) -> list[tuple[Customer, int]]:
    customers = session.scalars(
        select(Customer).where(Customer.shop_id == shop_id)
    ).all()
    balances = [
        (customer, customer_balance(session, shop_id, customer.id))
        for customer in customers
    ]
    return sorted(
        [(customer, balance) for customer, balance in balances if balance > 0],
        key=lambda pair: (
            -pair[1],
            pair[0].name.casefold(),
            (pair[0].label or "").casefold(),
        ),
    )


def ledger_rows(
    session: Session, shop_id: int, customer_id: int
) -> list[dict[str, Any]]:
    entries = session.scalars(
        select(Entry)
        .where(
            Entry.shop_id == shop_id,
            Entry.customer_id == customer_id,
            Entry.voided_at.is_(None),
        )
        .order_by(Entry.entry_date, Entry.id)
    ).all()
    payments = session.scalars(
        select(Payment)
        .where(
            Payment.shop_id == shop_id,
            Payment.customer_id == customer_id,
            Payment.voided_at.is_(None),
        )
        .order_by(Payment.payment_date, Payment.id)
    ).all()
    events: list[tuple[date, datetime, int, dict[str, Any]]] = []
    events.extend(
        (
            row.entry_date,
            row.created_at,
            row.id,
            {
                "date": row.entry_date,
                "kind": "entry",
                "description": f"{row.item_name} {row.quantity_text or ''}".strip(),
                "amount": row.price_paise,
            },
        )
        for row in entries
    )
    events.extend(
        (
            row.payment_date,
            row.created_at,
            row.id,
            {
                "date": row.payment_date,
                "kind": "payment",
                "description": "Payment",
                "amount": -row.amount_paise,
            },
        )
        for row in payments
    )
    events.sort(key=lambda event: (event[0], event[1], event[2]))
    balance = 0
    rows = []
    for _day, _created_at, _id, event in events:
        balance += event["amount"]
        rows.append({**event, "balance": balance})
    return rows


def record_entry(
    session: Session,
    shop_id: int,
    customer_id: int,
    entry_date: date,
    items: list[dict[str, Any]],
    created_by: int,
) -> int:
    for item in items:
        session.add(
            Entry(
                shop_id=shop_id,
                customer_id=customer_id,
                entry_date=entry_date,
                item_name=item["name"],
                quantity_text=item.get("quantity"),
                price_paise=item["price_paise"],
                created_by=created_by,
            )
        )
    session.flush()
    return customer_balance(session, shop_id, customer_id)


def record_opening_balance(
    session: Session,
    shop_id: int,
    customer_id: int,
    entry_date: date,
    amount_paise: int,
    created_by: int,
) -> int:
    """Record an imported starting debt as a distinct, auditable ledger row."""
    return record_entry(
        session,
        shop_id,
        customer_id,
        entry_date,
        [{"name": OPENING_BALANCE_ITEM, "price_paise": amount_paise}],
        created_by,
    )


def record_payment(
    session: Session,
    shop_id: int,
    customer_id: int,
    payment_date: date,
    amount_paise: int,
    created_by: int,
) -> int:
    session.add(
        Payment(
            shop_id=shop_id,
            customer_id=customer_id,
            payment_date=payment_date,
            amount_paise=amount_paise,
            created_by=created_by,
        )
    )
    session.flush()
    return customer_balance(session, shop_id, customer_id)


def audit(
    session: Session, shop_id: int, user_id: int, action: str, details: dict[str, Any]
) -> None:
    session.add(
        AuditLog(
            shop_id=shop_id,
            user_id=user_id,
            action=action,
            details=details,
        )
    )


def void_customer_ledger(
    session: Session,
    shop_id: int,
    customer_id: int,
    user_id: int,
) -> tuple[int, int]:
    """Soft-void all active customer ledger rows, retaining their audit trail."""
    now = utc_now()
    entries = session.execute(
        update(Entry)
        .where(
            Entry.shop_id == shop_id,
            Entry.customer_id == customer_id,
            Entry.voided_at.is_(None),
        )
        .values(voided_at=now)
    ).rowcount or 0
    payments = session.execute(
        update(Payment)
        .where(
            Payment.shop_id == shop_id,
            Payment.customer_id == customer_id,
            Payment.voided_at.is_(None),
        )
        .values(voided_at=now)
    ).rowcount or 0
    audit(
        session,
        shop_id,
        user_id,
        "customer_ledger_cleared",
        {"customer_id": customer_id, "entries_voided": entries, "payments_voided": payments},
    )
    return entries, payments


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_naive() -> datetime:
    """Backward-compatible alias; timestamps returned are aware UTC."""
    return utc_now()
