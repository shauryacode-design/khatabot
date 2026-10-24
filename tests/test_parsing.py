import asyncio
import json
from datetime import date

import httpx
import pytest
from cryptography.fernet import Fernet

from khata_bot.config import Settings
from khata_bot.parsing.llm_parser import parse_message
from khata_bot.parsing.rules import parse_with_rules

TODAY = date(2026, 10, 20)


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ADMIN_TELEGRAM_ID": "123",
            "GROQ_API_KEY": "test-key",
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        }
    )


@pytest.mark.parametrize(
    "customer,item,quantity,price,suffix",
    [
        (customer, item, quantity, price, suffix)
        for customer in ("Aman", "Shaurya", "Ramesh", "Ramesh ji", "Aman bhai")
        for item, quantity in (("aata", "1kg"), ("chini", "2 kg"))
        for price, suffix in (("45", ""), ("45", "rs"), ("₹45", ""), ("45", "rupaye"))
    ],
)
def test_rule_parser_handles_40_compact_entry_variants(
    customer: str, item: str, quantity: str, price: str, suffix: str
) -> None:
    parsed = parse_with_rules(
        f"  {customer}  -  {item} {quantity} {price} {suffix}  ", TODAY
    )

    assert parsed.intent == "entry"
    assert parsed.customer is not None
    assert parsed.customer.casefold().startswith(customer.split()[0].casefold())
    assert len(parsed.items) == 1
    assert parsed.items[0].name == item
    assert parsed.items[0].quantity is not None
    assert parsed.items[0].price == 45


@pytest.mark.parametrize(
    "text,customer,item_names",
    [
        (
            "Aman - chini 1kg 45, aata 10kg 400",
            "Aman",
            ["chini", "aata"],
        ),
        ("Shaurya milk powder 100", "Shaurya", ["milk powder"]),
        (
            "chawal 5kg Aman 200rs kal ka",
            "Aman",
            ["chawal"],
        ),
        ("Aman ko 2 kilo aata diya 90", "Aman", ["aata"]),
        ("Ramesh ji tel 150", "Ramesh", ["tel"]),
        (
            "Shaurya aata10kg 250, chinni 1kg 100",
            "Shaurya",
            ["aata", "chinni"],
        ),
    ],
)
def test_named_flexible_inputs(
    text: str, customer: str, item_names: list[str]
) -> None:
    parsed = parse_with_rules(text, TODAY)
    assert parsed.intent == "entry"
    assert parsed.customer == customer
    assert [item.name for item in parsed.items] == item_names
    assert all(item.price is not None for item in parsed.items)


@pytest.mark.parametrize(
    "text,intent,customer,amount",
    [
        ("Aman paid 500", "payment", "Aman", 500),
        ("Aman ne 500 diye", "payment", "Aman", 500),
        ("Aman jama 500", "payment", "Aman", 500),
        ("Aman paid ₹500", "payment", "Aman", 500),
        ("Aman ne 500 rupaye diye", "payment", "Aman", 500),
    ],
)
def test_rule_parser_payment_variants(
    text: str, intent: str, customer: str, amount: int
) -> None:
    parsed = parse_with_rules(text, TODAY)
    assert parsed.intent == intent
    assert parsed.customer == customer
    assert parsed.amount == amount


@pytest.mark.parametrize(
    "text,query_type,customer",
    [
        ("sabka hisab", "all", None),
        ("sabhi customers ka balance", "all", None),
        ("aaj ka hisab", "today", None),
        ("today entries", "today", None),
        ("Aman ka hisab", "customer_ledger", "Aman"),
        ("Aman ka kitna baaki hai", "customer_ledger", "Aman"),
        ("Aman", "customer_ledger", "Aman"),
    ],
)
def test_rule_parser_query_variants(
    text: str, query_type: str, customer: str | None
) -> None:
    parsed = parse_with_rules(text, TODAY)
    assert parsed.intent == "query"
    assert parsed.query_type == query_type
    assert parsed.customer == customer


@pytest.mark.parametrize(
    "text,expected",
    [
        ("aaj", date(2026, 10, 20)),
        ("kal", date(2026, 10, 19)),
        ("tomorrow", date(2026, 10, 21)),
        ("parso", date(2026, 10, 22)),
        ("12 oct", date(2026, 10, 12)),
        ("12/10", date(2026, 10, 12)),
    ],
)
def test_rule_parser_date_variants(text: str, expected: date) -> None:
    assert parse_with_rules(text, TODAY).date == expected


def test_rule_parser_returns_friendly_conversation() -> None:
    parsed = parse_with_rules("Hey what's up", TODAY)
    assert parsed.intent == "other"
    assert parsed.reply
    assert "Hello sir" in parsed.reply


def test_rule_parser_detects_customer_ledger_clear_request() -> None:
    parsed = parse_with_rules("Avinash ka hisab delete krdo", TODAY)

    assert parsed.intent == "delete_ledger"
    assert parsed.customer == "Avinash"


def test_rule_parser_reads_multiple_opening_balances() -> None:
    parsed = parse_with_rules(
        "Shaurya pending balance 5000\n"
        "Akash pending balance ₹2,000\n"
        "Avinash opening balance is 20000",
        TODAY,
    )

    assert parsed.intent == "opening_balance"
    assert [
        (item.customer, item.amount) for item in parsed.opening_balances
    ] == [
        ("Shaurya", 5000),
        ("Akash", 2000),
        ("Avinash", 20000),
    ]


def test_rule_parser_explains_opening_balance_format() -> None:
    parsed = parse_with_rules(
        "mere pass purane customers ka khata hai, pending balance bataunga"
    )

    assert parsed.intent == "opening_balance_help"
    assert "Shaurya pending balance 5000" in parsed.reply


def test_rule_parser_understands_multiline_price_before_item_with_memory() -> None:
    parsed = parse_with_rules(
        "chawal 300\ndaal 200",
        TODAY,
        last_customer="Katyani",
    )

    assert parsed.intent == "entry"
    assert parsed.customer == "Katyani"
    assert [(item.name, item.price) for item in parsed.items] == [
        ("chawal", 300),
        ("daal", 200),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Aman chini 1kg -45",
        "Aman chini 1kg 0",
        "Aman chini 1kg 10pct",
        "Aman chini 1kg",
        "Aman chini -45",
        "Aman chini 0",
        "Aman chini 10pct",
        "Aman paid -200",
        "Aman paid 0",
        "Aman paid 10pct",
        "Aman paid",
    ],
)
def test_rule_parser_rejects_invalid_money(text: str) -> None:
    assert parse_with_rules(text, TODAY).intent == "invalid_input"


def test_rule_parser_accepts_multiline_items_with_inherited_customer() -> None:
    parsed = parse_with_rules("Aman aata 10kg 400\nchini 2kg 100", TODAY)

    assert parsed.intent == "entry"
    assert parsed.customer == "Aman"
    assert [(item.name, item.price) for item in parsed.items] == [
        ("aata", 400),
        ("chini", 100),
    ]


def test_rule_parser_extracts_customer_and_multiline_items() -> None:
    parsed = parse_with_rules("Katyani 300 chawal\n200 daal", TODAY)

    assert parsed.intent == "entry"
    assert parsed.customer == "Katyani"
    assert [(item.name, item.price) for item in parsed.items] == [
        ("chawal", 300),
        ("daal", 200),
    ]


def test_groq_success_is_validated_and_uses_json_mode() -> None:
    request_payloads = []

    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        request_payloads.append(body)
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "intent": "entry",
                                "customer": "Aman",
                                "date": "2026-10-20",
                                "items": [
                                    {"name": "aata", "quantity": "10 kg", "price": 250}
                                ],
                                "amount": None,
                                "query_type": None,
                            }
                        )
                    }
                }
            ]
        }
        return httpx.Response(200, json=response)

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    parsed, fallback = asyncio.run(
        parse_message("Shaurya aata10kg 250", _settings(), TODAY, client)
    )
    asyncio.run(client.aclose())

    assert not fallback
    assert parsed.customer == "Aman"
    assert parsed.items[0].price == 250
    assert request_payloads[0]["temperature"] == 0
    assert request_payloads[0]["response_format"] == {"type": "json_object"}
    assert "2026-10-20" in request_payloads[0]["messages"][1]["content"]


def test_groq_invalid_json_retries_once_then_uses_rules() -> None:
    calls = 0

    def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not json"}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    parsed, fallback = asyncio.run(
        parse_message("Aman - aata 1kg 45", _settings(), TODAY, client)
    )
    asyncio.run(client.aclose())

    assert calls == 2
    assert fallback
    assert parsed.intent == "entry"
    assert parsed.customer == "Aman"


def test_groq_json_mode_rejection_retries_without_response_format() -> None:
    modes = []

    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        modes.append(body.get("response_format"))
        if len(modes) == 1:
            return httpx.Response(
                400,
                json={"error": {"message": "response_format is not supported"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"intent":"other","reply":"Hello sir!"}'}}
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    parsed, fallback = asyncio.run(
        parse_message("Hello", _settings(), TODAY, client)
    )
    asyncio.run(client.aclose())

    assert modes == [{"type": "json_object"}, None]
    assert parsed.intent == "other"
    assert not fallback


def test_groq_model_retired_uses_rule_fallback() -> None:
    def responder(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"code": "model_decommissioned", "message": "retired"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    parsed, fallback = asyncio.run(
        parse_message("Hello", _settings(), TODAY, client)
    )
    asyncio.run(client.aclose())

    assert fallback
    assert parsed.intent == "other"
