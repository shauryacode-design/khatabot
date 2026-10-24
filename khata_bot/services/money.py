"""Exact money conversion and Indian currency formatting."""

from decimal import Decimal, InvalidOperation


class InvalidMoney(ValueError):
    """Raised when a message contains an invalid or non-positive amount."""


def rupees_to_paise(value: str | int | Decimal) -> int:
    try:
        amount = Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except InvalidOperation as error:
        raise InvalidMoney("Amount must be a number.") from error
    if not amount.is_finite() or amount <= 0:
        raise InvalidMoney("Amount must be greater than zero.")
    if amount.as_tuple().exponent < -2:
        raise InvalidMoney("Amounts can have at most two decimal places.")
    return int(amount * 100)


def format_paise(amount_paise: int) -> str:
    """Format signed paise as ₹1,23,456.78 without floating-point arithmetic."""
    sign = "-" if amount_paise < 0 else ""
    rupees, paise = divmod(abs(amount_paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        tail = digits[-3:]
        head = digits[:-3]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        groups.insert(0, head)
        digits = ",".join(groups + [tail])
    fraction = f".{paise:02d}" if paise else ""
    return f"{sign}₹{digits}{fraction}"
