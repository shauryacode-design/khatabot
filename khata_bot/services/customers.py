"""Customer name normalization and shop-scoped fuzzy matching."""

import re
import unicodedata
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from khata_bot.models import Customer

_HONORIFICS = {"ji", "bhai", "sahab", "saab", "sir", "madam"}


def normalize_name(name: str) -> str:
    value = unicodedata.normalize("NFKC", name).casefold().strip()
    words = re.findall(r"[^\W_]+", value, flags=re.UNICODE)
    while words and words[-1] in _HONORIFICS:
        words.pop()
    return " ".join(words)


def customer_normalized_key(name: str, label: str | None = None) -> str:
    normalized = normalize_name(name)
    if label:
        return f"{normalized} {normalize_name(label)}"
    return normalized


def customer_matches(
    session: Session, shop_id: int, name: str, limit: int = 5
) -> list[Customer]:
    if "|" in name:
        customer_name, label = (part.strip() for part in name.split("|", 1))
        if not customer_name or not label:
            return []
        customers = session.scalars(
            select(Customer)
            .where(Customer.shop_id == shop_id)
            .order_by(Customer.name, Customer.label)
        ).all()
        return [
            customer
            for customer in customers
            if normalize_name(customer.name) == normalize_name(customer_name)
            and (customer.label or "").casefold() == label.casefold()
        ][:limit]
    normalized = normalize_name(name)
    if not normalized:
        return []
    customers = session.scalars(
        select(Customer)
        .where(Customer.shop_id == shop_id)
        .order_by(Customer.name)
    ).all()
    exact = [
        customer
        for customer in customers
        if normalize_name(customer.name) == normalized
    ]
    if exact:
        return sorted(exact, key=lambda item: (item.label is not None, item.label or ""))[:limit]
    ranked = [
        (
            max(
                SequenceMatcher(
                    None, normalized, normalize_name(customer.name)
                ).ratio(),
                max(
                    (
                        SequenceMatcher(None, normalized, word).ratio()
                        for word in normalize_name(customer.name).split()
                    ),
                    default=0,
                ),
            ),
            customer,
        )
        for customer in customers
    ]
    ranked = [item for item in ranked if item[0] >= 0.68]
    ranked.sort(key=lambda item: (-item[0], item[1].name.casefold()))
    return [
        customer
        for _score, customer in ranked[:limit]
    ]


def customer_display_name(customer: Customer) -> str:
    return f"{customer.name} ({customer.label})" if customer.label else customer.name


def create_customer(
    session: Session, shop_id: int, name: str, label: str | None = None
) -> Customer:
    clean_name = " ".join(name.strip().split())
    normalized = normalize_name(clean_name)
    if not normalized:
        raise ValueError("Customer name is required.")
    clean_label = " ".join(label.split())[:80] if label else None
    normalized_key = customer_normalized_key(clean_name, clean_label)
    existing = session.scalar(
        select(Customer.id).where(
            Customer.shop_id == shop_id,
            Customer.normalized_name == normalized_key,
        )
    )
    if existing is not None:
        raise ValueError("Customer with the same name and label already exists.")
    customer = Customer(
        shop_id=shop_id,
        name=clean_name,
        normalized_name=normalized_key,
        label=clean_label,
    )
    session.add(customer)
    session.flush()
    return customer
