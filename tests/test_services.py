from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from khata_bot.models import Base, Customer, Entry, Payment, Shop
from khata_bot.services.customers import customer_matches, normalize_name
from khata_bot.services.dates import month_bounds, parse_date
from khata_bot.services.ledger import all_balances, customer_balance, ledger_rows
from khata_bot.services.money import InvalidMoney, format_paise, rupees_to_paise


def test_money_uses_integer_paise_and_indian_grouping() -> None:
    assert rupees_to_paise(Decimal("125000.50")) == 12_500_050
    assert format_paise(12_500_050) == "₹1,25,000.50"
    assert format_paise(0) == "₹0"
    with pytest.raises(InvalidMoney):
        rupees_to_paise("1.239")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("aaj", date(2026, 10, 20)),
        ("kal", date(2026, 10, 21)),
        ("parso", date(2026, 10, 22)),
        ("12 oct", date(2026, 10, 12)),
        ("12/10", date(2026, 10, 12)),
        ("२०/१०", date(2026, 10, 20)),
    ],
)
def test_date_parser_handles_local_dates(text: str, expected: date) -> None:
    assert parse_date(text, date(2026, 10, 20)) == expected


def test_month_bounds_include_year_transition() -> None:
    assert month_bounds(2026, 12) == (date(2026, 12, 1), date(2027, 1, 1))


def test_customer_normalization_strips_honorifics() -> None:
    assert normalize_name("  AMAN ji ") == "aman"


def test_ledger_balances_and_name_matching_are_shop_scoped() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        shop_a = Shop(name="A")
        shop_b = Shop(name="B")
        session.add_all([shop_a, shop_b])
        session.flush()
        aman_a = Customer(shop_id=shop_a.id, name="Aman Verma", normalized_name="aman verma")
        aman_b = Customer(shop_id=shop_b.id, name="Aman Verma", normalized_name="aman verma")
        session.add_all([aman_a, aman_b])
        session.flush()
        session.add_all(
            [
                Entry(
                    shop_id=shop_a.id,
                    customer_id=aman_a.id,
                    entry_date=date(2026, 10, 20),
                    item_name="aata",
                    price_paise=25_000,
                    created_by=1,
                ),
                Payment(
                    shop_id=shop_a.id,
                    customer_id=aman_a.id,
                    payment_date=date(2026, 10, 20),
                    amount_paise=5_000,
                    created_by=1,
                ),
                Entry(
                    shop_id=shop_b.id,
                    customer_id=aman_b.id,
                    entry_date=date(2026, 10, 20),
                    item_name="chawal",
                    price_paise=99_000,
                    created_by=2,
                ),
            ]
        )
        session.commit()
        assert customer_balance(session, shop_a.id, aman_a.id) == 20_000
        assert customer_balance(session, shop_a.id, aman_b.id) == 0
        assert all_balances(session, shop_a.id) == [(aman_a, 20_000)]
        assert [row["balance"] for row in ledger_rows(session, shop_a.id, aman_a.id)] == [
            25_000,
            20_000,
        ]
        assert customer_matches(session, shop_a.id, "Aman ji")[0].id == aman_a.id
    engine.dispose()
