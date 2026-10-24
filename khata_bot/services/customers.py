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


def customer_matches(
    session: Session, shop_id: int, name: str, limit: int = 5
) -> list[Customer]:
    normalized = normalize_name(name)
    if not normalized:
        return []
    customers = session.scalars(
        select(Customer)
        .where(Customer.shop_id == shop_id)
        .order_by(Customer.name)
    ).all()
    exact = [customer for customer in customers if customer.normalized_name == normalized]
    if exact:
        return exact[:limit]
    ranked = [
        (
            max(
                SequenceMatcher(None, normalized, customer.normalized_name).ratio(),
                max(
                    (
                        SequenceMatcher(None, normalized, word).ratio()
                        for word in customer.normalized_name.split()
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
    return [customer for _score, customer in ranked[:limit]]


def create_customer(session: Session, shop_id: int, name: str) -> Customer:
    clean_name = " ".join(name.strip().split())
    normalized = normalize_name(clean_name)
    if not normalized:
        raise ValueError("Customer name is required.")
    customer = Customer(
        shop_id=shop_id, name=clean_name, normalized_name=normalized
    )
    session.add(customer)
    session.flush()
    return customer
