"""Shop CSV and customer PDF exports."""

import csv
import io
from datetime import date, datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session
from unidecode import unidecode

from khata_bot.models import Customer, Entry, Payment
from khata_bot.services.dates import IST
from khata_bot.services.ledger import (
    OPENING_BALANCE_ITEM,
    all_balances,
    ledger_rows,
)
from khata_bot.services.money import format_paise
from khata_bot.services.phones import decrypt_phone


def _timestamp_ist(value: datetime) -> str:
    return value.astimezone(IST).isoformat()


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
            "customer_label",
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
    customer_labels = {customer.id: customer.label or "" for customer in customer_rows}
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
                customer.label or "",
                phones[customer.id],
                "customer",
                (
                    customer.created_at.astimezone(IST).date().isoformat()
                    if customer.created_at
                    else ""
                ),
                "",
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
                customer_labels.get(entry.customer_id, ""),
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
                _timestamp_ist(entry.voided_at) if entry.voided_at else "",
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
                customer_labels.get(payment.customer_id, ""),
                phones.get(payment.customer_id, ""),
                "payment",
                payment.payment_date.isoformat(),
                "",
                "",
                payment.amount_paise,
                _timestamp_ist(payment.voided_at) if payment.voided_at else "",
            ]
        )
    outstanding_total = sum(balance for _customer, balance in all_balances(session, shop_id))
    writer.writerow(
        [
            "",
            "",
            "",
            "shop_outstanding_total",
            "",
            "",
            "",
            outstanding_total,
            "",
        ]
    )
    return output.getvalue().encode("utf-8-sig")


def export_customer_statement_pdf(
    session: Session,
    shop_id: int,
    customer_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> bytes:
    customer = session.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.shop_id == shop_id,
        )
    )
    if customer is None:
        raise ValueError("Customer does not belong to this shop.")
    rows = ledger_rows(session, shop_id, customer_id)
    if start_date is not None:
        rows = [row for row in rows if row["date"] >= start_date]
    if end_date is not None:
        rows = [row for row in rows if row["date"] < end_date]

    def pdf_text(value: str) -> str:
        return escape(unidecode(value))

    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title=unidecode(f"{customer.name} khata statement"),
    )
    styles = getSampleStyleSheet()
    title = customer.name
    if customer.label:
        title += f" ({customer.label})"
    elements = [
        Paragraph(pdf_text(title), styles["Title"]),
        Paragraph("Customer ledger statement", styles["Heading2"]),
        Paragraph(
            "Amounts in rupees. Payments reduce the balance. "
            "Text is transliterated to Latin characters for PDF font compatibility.",
            styles["BodyText"],
        ),
    ]
    data = [["Date", "Item / transaction", "Amount", "Running balance"]]
    for row in rows:
        amount = row["amount"]
        description = (
            "Payment received"
            if row["kind"] == "payment"
            else row["description"]
        )
        amount_text = format_paise(abs(amount)).replace("₹", "Rs. ")
        signed_amount = f"-{amount_text}" if amount < 0 else f"+{amount_text}"
        data.append(
            [
                row["date"].strftime("%d %b %Y"),
                Paragraph(pdf_text(description), styles["BodyText"]),
                signed_amount,
                format_paise(row["balance"]).replace("₹", "Rs. "),
            ]
        )
    final_balance = rows[-1]["balance"] if rows else 0
    data.append(
        [
            "",
            "Closing balance",
            "",
            format_paise(final_balance).replace("₹", "Rs. "),
        ]
    )
    table = Table(data, repeatRows=1, colWidths=[24 * mm, 75 * mm, 34 * mm, 42 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#245b4a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5d1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e7efeb")),
                ("FONTNAME", (1, -1), (-1, -1), "Helvetica-Bold"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(table)
    document.build(elements)
    return output.getvalue()
