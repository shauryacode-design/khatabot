"""Optional smoke coverage against a dedicated PostgreSQL test database."""

import os
import uuid
from datetime import date

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete

from khata_bot.config import Settings
from khata_bot.database import create_session_factory
from khata_bot.models import Customer, Entry, Payment, Shop
from khata_bot.services.exports import export_shop_csv
from khata_bot.services.ledger import customer_balance, record_entry, record_payment

POSTGRES_TEST_DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not POSTGRES_TEST_DATABASE_URL,
    reason="Set POSTGRES_TEST_DATABASE_URL to a dedicated disposable database.",
)
def test_postgres_scoped_ledger_and_export_balance() -> None:
    settings = Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "postgres-test-token",
            "ADMIN_TELEGRAM_ID": "123456",
            "GROQ_API_KEY": "postgres-test-key",
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "DATABASE_URL": POSTGRES_TEST_DATABASE_URL,
        }
    )
    sessions = create_session_factory(settings)
    engine = sessions.kw["bind"]
    shop_name = f"postgres-test-{uuid.uuid4()}"
    try:
        with sessions.begin() as session:
            shop_a = Shop(name=shop_name)
            shop_b = Shop(name=f"{shop_name}-other")
            session.add_all([shop_a, shop_b])
            session.flush()
            customer_a = Customer(
                shop_id=shop_a.id, name="Aman", normalized_name="aman"
            )
            customer_b = Customer(
                shop_id=shop_b.id, name="Aman", normalized_name="aman"
            )
            session.add_all([customer_a, customer_b])
            session.flush()
            record_entry(
                session,
                shop_a.id,
                customer_a.id,
                date(2026, 10, 20),
                [{"name": "chini", "quantity": "1 kg", "price_paise": 4_500}],
                5_726_633_609,
            )
            record_payment(
                session,
                shop_a.id,
                customer_a.id,
                date(2026, 10, 20),
                1_000,
                5_726_633_609,
            )
            record_entry(
                session,
                shop_b.id,
                customer_b.id,
                date(2026, 10, 20),
                [{"name": "aata", "price_paise": 99_000}],
                5_726_633_609,
            )
            assert customer_balance(session, shop_a.id, customer_a.id) == 3_500
            assert customer_balance(session, shop_a.id, customer_b.id) == 0
            export = export_shop_csv(session, shop_a.id).decode("utf-8-sig")
            assert "shop_outstanding_total" in export
            assert "aata" not in export
            assert "99000" not in export
    finally:
        with sessions.begin() as session:
            session.execute(delete(Shop).where(Shop.name.in_([shop_name, f"{shop_name}-other"])))
        engine.dispose()
