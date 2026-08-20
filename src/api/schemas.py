"""Request/response models for the local API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClassifyRequest(BaseModel):
    with_ai: bool = False


class AIAnalyzeRequest(BaseModel):
    mode: str = Field(default="incremental", pattern="^(full|incremental)$")


class AIProposalDecision(BaseModel):
    proposal_id: str = Field(min_length=8, max_length=80)
    action: str = Field(default="accept", pattern="^(accept|reject|defer)$")
    recommendation: dict | None = None
    save_as_rule: bool = False


class AIProposalDecisionsRequest(BaseModel):
    decisions: list[AIProposalDecision] = Field(min_length=1, max_length=500)


class ReviewDecision(BaseModel):
    category: str
    subcategory: str = ""
    tags: list[str] = Field(default_factory=list)
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


class MerchantUpdate(BaseModel):
    canonical: str | None = None
    aliases: list[AliasIn] | None = None
    category: str | None = None
    subcategory: str | None = None
    apply_category: bool = True
    restamp: bool = True


class MerchantMerge(BaseModel):
    source: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=120)
    apply_category: bool = False


class RuleIn(BaseModel):
    merchant_regex: str | None = None
    merchant_canonical: str | None = None
    category: str
    subcategory: str = ""


class RuleUpdate(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    subcategory: str = ""


class CategoryIn(BaseModel):
    category: str = Field(min_length=1, max_length=80)


class SubcategoryIn(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    subcategory: str = Field(min_length=1, max_length=80)


class TagIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    kind: str = Field(default="other", pattern="^(occasion|trip|other)$")
    id: str | None = Field(default=None, max_length=80)


class CardProductIn(BaseModel):
    issuer: str = Field(min_length=1, max_length=80)
    product: str = Field(min_length=1, max_length=80)


class InboxDeleteIn(BaseModel):
    path: str = Field(min_length=1, max_length=240)


class CardholderAssignIn(BaseModel):
    issuer: str = Field(min_length=1, max_length=80)
    product: str = Field(min_length=1, max_length=80)
    cardholder: str = Field(min_length=1, max_length=80)


class UploadCommitItem(BaseModel):
    token: str = Field(min_length=32, max_length=32, pattern="^[a-f0-9]+$")
    issuer: str | None = None
    product: str | None = Field(default=None, max_length=80)
    cardholder: str | None = Field(default=None, max_length=80)


class UploadCommitRequest(BaseModel):
    items: list[UploadCommitItem] = Field(min_length=1, max_length=20)


class InsightsChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    # User questions stay at 500 chars in the UI. Assistant replies are longer, and
    # the second turn sends that prior reply as history. insights._clip_messages
    # still trims each message before the model sees it.
    content: str = Field(min_length=1, max_length=4000)


class InsightsChatRequest(BaseModel):
    messages: list[InsightsChatMessage] = Field(min_length=1, max_length=8)


class ReviewPreviewRequest(BaseModel):
    txn_id: str
    category: str
    subcategory: str = ""
    rule_scope: str = Field(default="auto", pattern="^(auto|canonical|regex|none)$")


class BulkTransactionsRequest(BaseModel):
    txn_ids: list[str] = Field(min_length=1, max_length=2000)
    category: str | None = None
    subcategory: str | None = None
    tags: list[str] | None = None
    add_tags: list[str] | None = None


class BudgetSubcategoryIn(BaseModel):
    subcategory: str = Field(min_length=1, max_length=80)
    amount: float | None = None
    show_on_overview: bool = False


class BudgetEnvelopeIn(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    amount: float | None = None
    show_on_overview: bool = False
    subcategories: list[BudgetSubcategoryIn] = Field(default_factory=list)


class BudgetPut(BaseModel):
    envelopes: list[BudgetEnvelopeIn] = Field(default_factory=list)


class CategoryDelete(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    subcategory: str = ""
    action: str = Field(pattern="^(unassign|reassign)$")
    reassign_category: str = ""
    reassign_subcategory: str = ""
