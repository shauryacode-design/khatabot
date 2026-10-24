from datetime import date, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from khata_bot.models import Base, Customer, Entry, PendingConfirmation, Shop, User
from khata_bot.services.deletion import delete_exported_shop_data
from khata_bot.services.ledger import utc_now_naive


def test_delete_exported_shop_data_is_scoped_to_one_shop() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first_shop = Shop(name="First")
        other_shop = Shop(name="Other")
        session.add_all([first_shop, other_shop])
        session.flush()
        first_user = User(telegram_user_id=101, shop_id=first_shop.id)
        other_user = User(telegram_user_id=202, shop_id=other_shop.id)
        session.add_all([first_user, other_user])
        session.flush()
        first_customer = Customer(
            shop_id=first_shop.id, name="Aman", normalized_name="aman"
        )
        other_customer = Customer(
            shop_id=other_shop.id, name="Ravi", normalized_name="ravi"
        )
        session.add_all([first_customer, other_customer])
        session.flush()
        session.add_all(
            [
                Entry(
                    shop_id=first_shop.id,
                    customer_id=first_customer.id,
                    entry_date=date.today(),
                    item_name="aata",
                    price_paise=10_000,
                    created_by=101,
                ),
                Entry(
                    shop_id=other_shop.id,
                    customer_id=other_customer.id,
                    entry_date=date.today(),
                    item_name="chawal",
                    price_paise=20_000,
                    created_by=202,
                ),
            ]
        )
        confirmation = PendingConfirmation(
            shop_id=first_shop.id,
            user_id=first_user.id,
            kind="delete_stage2",
            payload={},
            created_at=utc_now_naive(),
            expires_at=utc_now_naive() + timedelta(minutes=30),
            status="pending",
        )
        session.add(confirmation)
        session.flush()
        first_shop_id = first_shop.id
        other_shop_id = other_shop.id
        confirmation_id = confirmation.id
        first_user_id = first_user.id
        assert delete_exported_shop_data(
            session, first_shop_id, confirmation_id, first_user_id
        )
        session.commit()

        assert session.scalar(select(func.count(Shop.id))) == 1
        assert session.scalar(select(Shop.id)) == other_shop_id
        assert session.scalar(
            select(func.count(Entry.id)).where(Entry.shop_id == other_shop_id)
        ) == 1
        assert not delete_exported_shop_data(
            session, first_shop_id, confirmation_id, first_user_id
        )
    engine.dispose()


def test_delete_exported_shop_data_rejects_expired_confirmation() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        shop = Shop(name="Expired")
        session.add(shop)
        session.flush()
        user = User(telegram_user_id=303, shop_id=shop.id)
        session.add(user)
        session.flush()
        confirmation = PendingConfirmation(
            shop_id=shop.id,
            user_id=user.id,
            kind="delete_stage2",
            payload={},
            created_at=utc_now_naive() - timedelta(hours=1),
            expires_at=utc_now_naive() - timedelta(minutes=30),
            status="pending",
        )
        session.add(confirmation)
        session.flush()

        assert not delete_exported_shop_data(
            session, shop.id, confirmation.id, user.id
        )
        assert session.scalar(select(func.count(Shop.id))) == 1
    engine.dispose()
