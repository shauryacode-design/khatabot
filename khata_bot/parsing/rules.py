"""Conservative fallback parser for common shopkeeper message formats."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from khata_bot.parsing.schemas import ParsedItem, ParsedMessage, ParsedOpeningBalance
from khata_bot.services.dates import parse_date

_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_MONEY = r"(?:₹\s*)?(\d[\d,]*(?:\.\d{1,2})?)(?:\s*(?:rs\.?|rupees?|rupaye|रुपये?))?"
_UNITS = (
    r"(?:kg|kgs|kilo(?:gram)?s?|g|gm|gram(?:s)?|l|lit(?:re|er)s?|ml|"
    r"pcs?|piece|packet|packets|dozen|किलो|ग्राम|लीटर)"
)


def _clean_customer(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = re.sub(r"^(?:customer\s*:\s*)", "", name.strip(), flags=re.I)
    cleaned = re.sub(r"\b(?:ji|bhai|sahab|saab)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^[\s,;:/-]+|[\s,;:/-]+$", "", cleaned)
    return " ".join(cleaned.split())[:120] or None


def _number(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _extract_items(value: str) -> list[ParsedItem]:
    items: list[ParsedItem] = []
    for segment in re.split(r",(?!\d)|[;\n]+", value):
        segment = segment.strip(" .")
        trailing_match = re.search(_MONEY + r"\s*$", segment, flags=re.I)
        leading_match = re.match(_MONEY + r"\s+(.+)$", segment, flags=re.I)
        if trailing_match is not None:
            amount = _number(trailing_match.group(1))
            description = segment[: trailing_match.start()].strip(" -:")
        elif leading_match is not None:
            amount = _number(leading_match.group(1))
            description = leading_match.group(2).strip(" -:")
        else:
            continue
        if amount is None or amount <= 0:
            continue
        if not description:
            continue
        quantity = None
        quantity_match = re.search(
            rf"(?<!\d)(\d+(?:\.\d+)?)\s*({_UNITS})\b",
            description,
            flags=re.I,
        )
        if quantity_match:
            quantity = f"{quantity_match.group(1)} {quantity_match.group(2)}"
            description = (
                description[: quantity_match.start()]
                + " "
                + description[quantity_match.end() :]
            )
        description = " ".join(description.split())
        description = re.sub(
            r"^(?:ji|bhai|sahab|saab)\s+", "", description, flags=re.I
        )
        if description:
            items.append(ParsedItem(name=description, quantity=quantity, price=amount))
    return items


def parse_with_rules(
    message: str,
    today: date | None = None,
    last_customer: str | None = None,
) -> ParsedMessage:
    value = message.translate(_DIGITS).strip()
    lowered = value.casefold()
    date_value = parse_date(value, today)
    asks_about_advance = re.search(
        r"\b(?:kisi|kisika|kis|who|anyone|any|kaun|kon|kya|ky|customer)\b",
        lowered,
    )
    mentions_advance = re.search(
        r"\b(?:advance|advances|peshgi)\b", lowered
    )
    if asks_about_advance and mentions_advance:
        return ParsedMessage(
            intent="query", query_type="advances", date=date_value
        )

    if re.search(
        r"\b(?:pending\s+balance|opening\s+balance|old\s+balance)\b",
        value,
        flags=re.I,
    ):
        opening_balances = []
        malformed = False
        for line in (line.strip() for line in value.splitlines() if line.strip()):
            match = re.fullmatch(
                rf"[\s\-•]*(?P<customer>.+?)\s+"
                rf"(?:pending\s+balance|opening\s+balance|old\s+balance)\s*"
                rf"(?:(?:is|:|=)\s*)?{_MONEY}\s*",
                line,
                flags=re.I,
            )
            if match is None:
                malformed = True
                break
            customer = _clean_customer(match.group("customer"))
            amount = _number(match.group(2))
            if customer is None or amount is None or amount <= 0:
                malformed = True
                break
            opening_balances.append(
                ParsedOpeningBalance(customer=customer, amount=amount)
            )
        if opening_balances and not malformed:
            if len(opening_balances) > 20:
                return ParsedMessage(
                    intent="opening_balance_help",
                    reply="Ek baar mein adhiktam 20 opening balances bhejiye.",
                )
            return ParsedMessage(
                intent="opening_balance",
                date=date_value,
                opening_balances=opening_balances,
            )
        if re.search(
            r"\b(?:purane|old)\s+customers?\b", value, flags=re.I
        ) and not re.search(r"\d", value):
            return ParsedMessage(
                intent="opening_balance_help",
                reply=(
                    "Haan, purane customers ka baki khata add kar sakte hain. "
                    "Har customer ko alag line mein is format mein bhejiye:\n"
                    "Shaurya pending balance 5000\n"
                    "Akash pending balance 2000"
                ),
            )
        return ParsedMessage(
            intent="opening_balance_help",
            reply=(
                "Opening balance ke liye har customer ko alag line mein "
                "'Naam pending balance rakam' format mein bhejiye. "
                "Rakam rupees mein honi chahiye."
            ),
        )
    if re.search(
        r"\b(?:delete|remove|clear|hata(?:o)?|mit(?:a|ao)|saaf)\b",
        lowered,
    ) and re.search(r"\b(?:hisab|khata|ledger|account)\b", lowered):
        customer = re.sub(
            r"\b(?:ka|ki|ke|hisab|khata|ledger|account|delete|remove|clear|"
            r"krdo|kardo|kar\s*do|hatao|hata|mita|mitao|saaf|please)\b",
            " ",
            value,
            flags=re.I,
        )
        return ParsedMessage(
            intent="delete_ledger",
            customer=_clean_customer(customer),
            date=date_value,
        )
    if re.search(r"\b(?:sabka|sabhi|all)\b.*\b(?:hisab|balance|ledger)\b", lowered):
        return ParsedMessage(intent="query", query_type="all", date=date_value)
    if re.search(r"\b(?:aaj|today)\b.*\b(?:hisab|entry|entries)\b", lowered):
        return ParsedMessage(intent="query", query_type="today", date=date_value)
    if re.search(r"\b(?:month|mahina|monthly)\b.*\b(?:hisab|report)\b", lowered):
        return ParsedMessage(intent="query", query_type="month", date=date_value)

    # Catch malformed monetary tails before the permissive name-query fallback.
    for line in (part.strip() for part in value.splitlines() if part.strip()):
        line_for_check = re.sub(
            r"\b(?:aaj|kal|parso|today|tomorrow|day after tomorrow)\b(?:\s+ka)?",
            " ",
            line,
            flags=re.I,
        )
        line_for_check = re.sub(
            r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", " ", line_for_check
        )
        line_for_check = re.sub(
            r"\b\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
            r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
            r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+20\d{2})?\b",
            " ",
            line_for_check,
            flags=re.I,
        )
        has_quantity = re.search(
            rf"\d+(?:\.\d+)?\s*{_UNITS}\b", line_for_check, flags=re.I
        )
        is_payment = re.search(
            r"\b(?:paid|pay|jama|diye|diya|de\s*diye|de\s*diya|payment)\b",
            line_for_check,
            flags=re.I,
        )
        has_suspicious_amount_tail = re.search(
            r"(?:^|\s)-\s*\d[\d,]*(?:\.\d{1,2})?\s*$", line_for_check
        ) or re.search(r"(?:^|\s)\d+[A-Za-z]+\s*$", line_for_check)
        likely_entry = len(line_for_check.split()) >= 3 and (
            re.search(r"(?:^|\s)\d[\d,]*(?:\.\d{1,2})?\s*$", line_for_check)
            or has_suspicious_amount_tail
        )
        if has_quantity or is_payment or likely_entry:
            if re.search(
                r"(?:^|\s)-\s*\d[\d,]*(?:\.\d{1,2})?(?:\s*(?:rs\.?|rupees?|rupaye?))?\s*$",
                line_for_check,
                flags=re.I,
            ):
                return ParsedMessage(
                    intent="invalid_input",
                    reply="Rakam negative nahi ho sakti. Sahi positive rakam ke saath dobara bhejiye.",
                )
            if (has_quantity or is_payment or likely_entry) and re.search(
                r"(?:^|\s)0+(?:\.0{1,2})?(?:\s*(?:rs\.?|rupees?|rupaye?))?\s*$",
                line_for_check,
                flags=re.I,
            ):
                return ParsedMessage(
                    intent="invalid_input",
                    reply="Rakam zero nahi ho sakti. Sahi positive rakam ke saath dobara bhejiye.",
                )
            if (has_quantity or likely_entry) and re.search(
                r"(?:^|\s)\d+[A-Za-z]+\s*$", line_for_check
            ) and not re.search(
                r"(?:rs\.?|rupees?|rupaye?)\s*$", line_for_check, flags=re.I
            ):
                return ParsedMessage(
                    intent="invalid_input",
                    reply="Rakam samajh nahi aayi. Har item ki total keemat rupees mein likhiye, jaise: Aman chini 1kg 45.",
                )
            if has_quantity and not re.search(
                _MONEY + r"\s*$", line_for_check, flags=re.I
            ):
                return ParsedMessage(
                    intent="invalid_input",
                    reply="Item ki total keemat chhoot gayi hai. Har item ke baad total rakam rupees mein likhiye.",
                )
            payment_amount = is_payment and re.search(
                rf"(?:\b(?:paid|pay|jama|diye|diya|payment)\s+|\bne\s+)"
                rf"{_MONEY}(?![\w.,])",
                line_for_check,
                flags=re.I,
            )
            if is_payment and not payment_amount:
                return ParsedMessage(
                    intent="invalid_input",
                    reply="Payment ki rakam samajh nahi aayi. Positive amount likhiye, jaise: Aman ne 200 diye.",
                )

    value = re.sub(
        r"\b(?:aaj|kal|parso|today|tomorrow|day after tomorrow)\b(?:\s+ka)?",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", " ", value)
    value = re.sub(
        r"\b\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
        r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+20\d{2})?\b",
        " ",
        value,
        flags=re.I,
    )
    lowered = value.casefold()

    if re.search(r"\b(?:hisab|balance|baaki|ledger|account)\b", lowered):
        name = re.sub(
            r"\b(?:ka|ki|ke|hisab|balance|baaki|hai|ledger|account|kitna|batao)\b",
            " ",
            value,
            flags=re.I,
        )
        return ParsedMessage(
            intent="query",
            customer=_clean_customer(name),
            date=date_value,
            query_type="customer_ledger",
        )

    hindi_entry = re.match(
        rf"^\s*(.+?)\s+(?:ko|ne)\s+(.+?)\s+(?:diya|di|liye|liya)\s+{_MONEY}\s*$",
        value,
        flags=re.I,
    )
    if hindi_entry:
        item_name = hindi_entry.group(2)
        item = _extract_items(f"{item_name} {hindi_entry.group(3)}")
        if not item:
            item = [
                ParsedItem(
                    name=item_name.strip(),
                    price=_number(hindi_entry.group(3)),
                )
            ]
        return ParsedMessage(
            intent="entry",
            customer=_clean_customer(hindi_entry.group(1)),
            date=date_value,
            items=item,
        )

    payment_match = re.search(
        rf"^\s*(.+?)\s+(?:ne\s+)?(?:paid|pay|jama|diye|diya|de\s*diye|payment)\s+{_MONEY}\s*$",
        value,
        flags=re.I,
    )
    if payment_match is None:
        payment_match = re.search(
            rf"^\s*(.+?)(?:\s+ne)?\s+{_MONEY}\s+"
            rf"(?:aur\s+)?(?:paid|jama|de\s*diye|de\s*diya|diye|diya)\s*$",
            value,
            flags=re.I,
        )
    if payment_match:
        customer_name = re.sub(r"\b(?:ko|ne)\s*$", "", payment_match.group(1), flags=re.I)
        return ParsedMessage(
            intent="payment",
            customer=_clean_customer(customer_name),
            date=date_value,
            amount=_number(payment_match.group(2)),
        )

    prefix = re.match(r"^\s*([^,-]{1,120}?)\s*[-:]\s*(.+)$", value, flags=re.S)
    if prefix:
        customer = _clean_customer(prefix.group(1))
        items = _extract_items(prefix.group(2))
        if items:
            return ParsedMessage(
                intent="entry", customer=customer, date=date_value, items=items
            )

    # A name at the start is common in compact shopkeeper messages.
    tokens = value.split()
    segments = re.split(r",(?!\d)", value)
    if len(segments) > 1 and len(tokens) >= 3:
        first_tokens = segments[0].split()
        if len(first_tokens) >= 3:
            first_items = _extract_items(" ".join(first_tokens[1:]))
            remaining_items = _extract_items(",".join(segments[1:]))
            if first_items and remaining_items:
                return ParsedMessage(
                    intent="entry",
                    customer=_clean_customer(first_tokens[0]),
                    date=date_value,
                    items=first_items + remaining_items,
                )
    if len(tokens) >= 3:
        segments = [
            segment.strip(" .")
            for segment in re.split(r",|[;\n]+", value)
            if segment.strip(" .,")
        ]
        if segments:
            first_words = segments[0].split(maxsplit=1)
            customer_first = (
                bool(first_words)
                and (
                    first_words[0][:1].isupper()
                    or (
                        last_customer is not None
                        and first_words[0].casefold()
                        == last_customer.casefold()
                    )
                )
            )
            if len(first_words) == 2:
                starts_with_quantity = re.match(
                    rf"^\s*\d+(?:\.\d+)?\s*{_UNITS}\b",
                    first_words[1],
                    flags=re.I,
                )
                if not starts_with_quantity or customer_first:
                    customer_candidate = _clean_customer(first_words[0])
                    customer_segments = [first_words[1], *segments[1:]]
                    customer_items = _extract_items("\n".join(customer_segments))
                    if len(customer_items) == len(segments):
                        return ParsedMessage(
                            intent="entry",
                            customer=customer_candidate,
                            date=date_value,
                            items=customer_items,
                        )
            all_items = _extract_items("\n".join(segments))
            if len(all_items) == len(segments) and (
                len(segments) > 1 or last_customer is not None
            ):
                return ParsedMessage(
                    intent="entry",
                    customer=_clean_customer(last_customer),
                    date=date_value,
                    items=all_items,
                )

        amount_match = re.search(_MONEY + r"\s*$", value, flags=re.I)
        before_amount = value[: amount_match.start()].strip() if amount_match else ""
        quantity_match = re.search(
            rf"(?<!\d)\d+(?:\.\d+)?\s*{_UNITS}\b", before_amount, flags=re.I
        )
        if quantity_match:
            item_name = before_amount[: quantity_match.start()].strip()
            customer_after_quantity = before_amount[quantity_match.end() :].strip()
            if item_name and customer_after_quantity and len(customer_after_quantity.split()) <= 3:
                item_text = (
                    f"{item_name} {quantity_match.group(0)} "
                    f"{amount_match.group(0)}"
                )
                return ParsedMessage(
                    intent="entry",
                    customer=_clean_customer(customer_after_quantity),
                    date=date_value,
                    items=_extract_items(item_text),
                )

        customer_first_items = _extract_items(" ".join(tokens[1:]))
        if customer_first_items:
            customer_first_items[0].name = re.sub(
                r"^(?:ji|bhai|sahab|saab)\s+", "", customer_first_items[0].name,
                flags=re.I,
            )
            return ParsedMessage(
                intent="entry",
                customer=_clean_customer(tokens[0]),
                date=date_value,
                items=customer_first_items,
            )
        items = _extract_items(value)
        if items:
            # Do not guess a customer when this fallback cannot find a boundary.
            return ParsedMessage(intent="entry", date=date_value, items=items)

    if re.search(
        r"\b(?:hello|hi|hey|namaste|hii|good morning|good evening|what'?s up|whats'?up)\b",
        lowered,
    ):
        return ParsedMessage(
            intent="other",
            date=date_value,
            reply="Hello sir! Main theek hoon 😊 Aap customer ka khata, payment ya balance dekhna chahte hain?",
        )
    if re.search(r"\b(?:date\??|what can you do|help|madad)\b", lowered):
        return ParsedMessage(
            intent="other",
            date=date_value,
            reply="Aap customer ka udhaar likh sakte hain, payment darj kar sakte hain, ya kisi ek/sabhi customers ka balance pooch sakte hain.",
        )
    if re.fullmatch(r"[\w .'-]{1,120}", value, flags=re.UNICODE):
        return ParsedMessage(
            intent="query",
            customer=_clean_customer(value),
            query_type="customer_ledger",
            date=date_value,
        )
    return ParsedMessage(
        intent="other",
        date=date_value,
        reply="Samajh gaya sir. Customer ka naam aur kya hua (samaan diya, payment mili, ya hisab chahiye) thoda aur bataiye.",
    )
