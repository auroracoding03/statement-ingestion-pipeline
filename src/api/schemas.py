"""Request/response models for the local API."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ClassifyRequest(BaseModel):
    with_ai: bool = False


class ReviewDecision(BaseModel):
    category: str
    subcategory: str = ""
    create_rule: bool = True
    # When the merchant has a canonical identity, prefer a canonical rule over a regex
    rule_scope: str = Field(default="auto", pattern="^(auto|canonical|regex|none)$")


class AliasIn(BaseModel):
    regex: str | None = None
    exact: str | None = None


class MerchantIn(BaseModel):
    canonical: str
    aliases: list[AliasIn] = Field(default_factory=list)
    members: list[str] = Field(default_factory=list)
    category: str | None = None
    subcategory: str | None = None
    restamp: bool = True


class RuleIn(BaseModel):
    merchant_regex: str | None = None
    merchant_canonical: str | None = None
    category: str
    subcategory: str = ""


class ObligationIn(BaseModel):
    name: str
    category: str
    subcategory: str = ""
    expected_amount_cents: int
    due_day: int


class ObligationOccurrenceIn(BaseModel):
    status: Literal["paid", "skipped"]
    actual_amount_cents: int | None = None
    paid_date: date | None = None
    note: str = ""

    @model_validator(mode="after")
    def check_status_fields(self) -> ObligationOccurrenceIn:
        if self.status == "paid":
            if self.actual_amount_cents is None or self.actual_amount_cents <= 0:
                raise ValueError("paid requires a positive actual_amount_cents")
            if self.paid_date is None:
                raise ValueError("paid requires paid_date")
        else:
            self.actual_amount_cents = None
            self.paid_date = None
        return self
