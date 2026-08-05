"""Request/response models for the local API."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
