from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from khata_bot.models import Base, Shop, User
from khata_bot.services.auth import find_authorized_user


def test_only_registered_telegram_users_are_authorized() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        shop = Shop(name="Aman Stores")
        session.add(shop)
        session.flush()
        session.add(User(telegram_user_id=111, shop_id=shop.id))
        session.commit()

        membership = find_authorized_user(session, 111)
        unknown = find_authorized_user(session, 222)

    assert membership is not None
    assert membership.shop.name == "Aman Stores"
    assert unknown is None
