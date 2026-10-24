import csv
import io
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from khata_bot.models import Base, Customer, Entry, Payment, Shop
from khata_bot.services.exports import (
    export_customer_statement_pdf,
    export_shop_csv,
)
from khata_bot.services.ledger import customer_balance


def test_csv_outstanding_total_matches_shop_balances_and_pdf_is_generated() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        shop = Shop(name="Export shop")
        session.add(shop)
        session.flush()
        customer = Customer(
            shop_id=shop.id,
            name="Aman",
            normalized_name="aman",
            label="Main market",
        )
        session.add(customer)
        session.flush()
        session.add_all(
            [
                Entry(
                    shop_id=shop.id,
                    customer_id=customer.id,
                    entry_date=date(2026, 10, 20),
                    item_name="chini",
                    quantity_text="1 kg",
                    price_paise=4_500,
                    created_by=5_726_633_609,
                ),
                Payment(
                    shop_id=shop.id,
                    customer_id=customer.id,
                    payment_date=date(2026, 10, 20),
                    amount_paise=1_000,
                    created_by=5_726_633_609,
                ),
            ]
        )
        session.flush()

        rows = list(csv.reader(io.StringIO(export_shop_csv(session, shop.id).decode("utf-8-sig"))))
        summary = next(row for row in rows if row[3] == "shop_outstanding_total")
        assert int(summary[7]) == customer_balance(session, shop.id, customer.id)

        pdf = export_customer_statement_pdf(session, shop.id, customer.id)
        assert pdf.startswith(b"%PDF-")
        assert len(pdf) > 1_000

    engine.dispose()
