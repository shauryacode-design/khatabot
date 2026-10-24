"""CSV export for a single shop."""

import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from khata_bot.models import Customer, Entry, Payment
from khata_bot.services.ledger import OPENING_BALANCE_ITEM
from khata_bot.services.phones import decrypt_phone


def export_shop_csv(
    session: Session,
    shop_id: int,
    encryption_key: str | None = None,
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "customer",
            "phone",
            "record_type",
            "date",
            "item",
            "quantity",
            "amount_paise",
            "voided_at",
        ]
    )
    customer_rows = session.scalars(
        select(Customer).where(Customer.shop_id == shop_id)
    ).all()
    customers = {customer.id: customer.name for customer in customer_rows}
    phones = {
        customer.id: (
            decrypt_phone(customer.phone_encrypted, encryption_key)
            if customer.phone_encrypted and encryption_key
            else ""
        )
        for customer in customer_rows
    }
    for customer in customer_rows:
        writer.writerow(
            [
                customer.name,
                phones[customer.id],
                "customer",
                customer.created_at.date().isoformat() if customer.created_at else "",
                "",
                "",
                "",
            ]
        )
    for entry in session.scalars(
        select(Entry)
        .where(Entry.shop_id == shop_id)
        .order_by(Entry.entry_date, Entry.id)
    ).all():
        writer.writerow(
            [
                customers.get(entry.customer_id, ""),
                phones.get(entry.customer_id, ""),
                (
                    "opening_balance"
                    if entry.item_name == OPENING_BALANCE_ITEM
                    else "entry"
                ),
                entry.entry_date.isoformat(),
                entry.item_name,
                entry.quantity_text or "",
                entry.price_paise,
                entry.voided_at.isoformat() if entry.voided_at else "",
            ]
        )
    for payment in session.scalars(
        select(Payment)
        .where(Payment.shop_id == shop_id)
        .order_by(Payment.payment_date, Payment.id)
    ).all():
        writer.writerow(
            [
                customers.get(payment.customer_id, ""),
                phones.get(payment.customer_id, ""),
                "payment",
                payment.payment_date.isoformat(),
                "",
                "",
                payment.amount_paise,
                payment.voided_at.isoformat() if payment.voided_at else "",
            ]
        )
    return output.getvalue().encode("utf-8-sig")
