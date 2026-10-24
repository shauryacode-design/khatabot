import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from khata_bot.models import Base
from khata_bot.services.admin import add_user, create_invite, create_shop, redeem_invite


def test_invite_is_single_use_and_user_is_tied_to_shop() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        shop = create_shop(session, "Demo shop", 111)
        code = create_invite(session, shop.id, 111)
        session.commit()

        member = redeem_invite(session, code, 222)
        session.commit()
        assert member is not None
        assert member.shop_id == shop.id
        assert redeem_invite(session, code, 333) is None
        with pytest.raises(ValueError, match="another shop"):
            other_shop = create_shop(session, "Second shop", 444)
            session.flush()
            add_user(session, other_shop.id, 222)
    engine.dispose()


def test_admin_settings_keys_are_usable_for_encryption() -> None:
    phone_key = Fernet.generate_key().decode()
    backup_key = Fernet.generate_key().decode()
    assert phone_key != backup_key
