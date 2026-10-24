"""Validated representation of an understood shopkeeper message."""

from datetime import date as DateType
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Intent = Literal[
    "entry",
    "payment",
    "opening_balance",
    "opening_balance_help",
    "invalid_input",
    "query",
    "delete_ledger",
    "other",
]
QueryType = Literal["customer_ledger", "all", "today", "month"]


class ParsedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    quantity: str | None = Field(default=None, max_length=80)
    price: Decimal | None = None

    @field_validator("price")
    @classmethod
    def valid_price(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value <= 0):
            raise ValueError("Item total must be greater than zero.")
        return value


class ParsedOpeningBalance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer: str = Field(min_length=1, max_length=120)
    amount: Decimal

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("Opening balance must be greater than zero.")
        return value


class ParsedMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Intent
    customer: str | None = Field(default=None, max_length=120)
    date: DateType | None = None
    items: list[ParsedItem] = Field(default_factory=list, max_length=20)
    amount: Decimal | None = None
    opening_balances: list[ParsedOpeningBalance] = Field(
        default_factory=list, max_length=20
    )
    query_type: QueryType | None = None
    reply: str | None = Field(default=None, max_length=1000)

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value <= 0):
            raise ValueError("Amount must be greater than zero.")
        return value
