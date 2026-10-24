"""Manage shops and registered Telegram accounts from the command line."""

import argparse

from sqlalchemy import func, select

from khata_bot.config import ConfigurationError, load_settings
from khata_bot.database import create_session_factory
from khata_bot.models import Shop, User
from khata_bot.services.admin import add_user, create_invite, create_shop


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage Khata Bot shops and Telegram users."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create_shop", help="Create a shop")
    create.add_argument("name", nargs="+", help="Shop name")
    add = commands.add_parser("add_user", help="Register a Telegram user")
    add.add_argument("shop_id", type=int)
    add.add_argument("telegram_id", type=int)
    commands.add_parser("list_shops", help="List shops and registered-user counts")
    invite = commands.add_parser("create_invite", help="Create a 7-day invite code")
    invite.add_argument("shop_id", type=int)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        settings = load_settings()
        session_factory = create_session_factory(settings)
    except ConfigurationError as error:
        parser.error(str(error))

    try:
        with session_factory.begin() as session:
            if args.command == "create_shop":
                shop = create_shop(
                    session,
                    " ".join(args.name),
                    settings.admin_telegram_id,
                )
                print(f"Created shop {shop.id}: {shop.name}")
            elif args.command == "add_user":
                add_user(session, args.shop_id, args.telegram_id)
                print(f"Telegram user added to shop {args.shop_id}.")
            elif args.command == "list_shops":
                shops = session.execute(
                    select(Shop, func.count(User.id))
                    .outerjoin(User, User.shop_id == Shop.id)
                    .group_by(Shop.id)
                    .order_by(Shop.id)
                ).all()
                if not shops:
                    print("No shops found.")
                for shop, user_count in shops:
                    print(f"{shop.id}\t{shop.name}\tusers={user_count}")
            elif args.command == "create_invite":
                code = create_invite(
                    session, args.shop_id, settings.admin_telegram_id
                )
                print("Send this one-time code to the new user:")
                print(f"/start {code}")
    except ValueError as error:
        parser.error(str(error))
    finally:
        if "session_factory" in locals():
            session_factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
