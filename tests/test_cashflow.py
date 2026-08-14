"""Charge vs monthly payment vs merchant return classification."""

from __future__ import annotations

import pandas as pd

from src.cashflow import is_payment_row, summarize_spend
from src.normalize import make_txn_id


def _row(**overrides):
    posted = overrides.get("posted_date", "2026-07-15")
    amount = overrides.get("amount", 12.0)
    raw = overrides.get("raw_description", "COFFEE SHOP")
    card = overrides.get("card", "chase")
    base = {
        "txn_id": make_txn_id(card, posted, amount, raw),
        "card": card,
        "card_issuer": "Chase",
        "card_product": "Sapphire Preferred",
        "cardholder": "Alex Example",
        "posted_date": posted,
        "amount": amount,
        "raw_description": raw,
        "normalized_merchant": raw,
        "canonical_merchant": overrides.get("canonical_merchant", raw.title()),
        "category": "Food",
        "subcategory": None,
        "tags": [],
    }
    base.update(overrides)
    return base


def test_monthly_payment_tag_is_payment_not_return():
    row = _row(amount=-200.0, raw_description="CHASE AUTOPAY", subcategory="Monthly Payment", category="Transfers")
    assert is_payment_row(row) is True
    stats = summarize_spend(pd.DataFrame([row, _row(amount=40.0)]))
    assert stats["payments_total"] == 200.0
    assert stats["returns_total"] == 0.0
    assert stats["net_spend"] == 40.0
    assert stats["gross_charges"] == 40.0


def test_legacy_cardpayment_still_counts_as_payment():
    row = _row(amount=-80.0, raw_description="WEB PAY", category="Transfers", subcategory="CardPayment")
    assert is_payment_row(row) is True


def test_amazon_refund_is_return_not_payment():
    row = _row(
        amount=-14.0,
        raw_description="AMAZON.COM",
        canonical_merchant="Amazon",
        category="Shopping",
    )
    assert is_payment_row(row) is False
    stats = summarize_spend(pd.DataFrame([_row(amount=53.0, canonical_merchant="Amazon"), row]))
    assert stats["gross_charges"] == 53.0
    assert stats["returns_total"] == 14.0
    assert stats["payments_total"] == 0.0
    assert stats["net_spend"] == 39.0


def test_payment_thank_you_description_is_payment_without_category():
    row = _row(amount=-200.0, raw_description="PAYMENT THANK YOU", category=None, subcategory=None)
    assert is_payment_row(row) is True


def test_monthly_payment_tag_id_on_row():
    row = _row(amount=-50.0, raw_description="AUTOPAY", tags=["monthly-payment"], category="Uncategorized")
    assert is_payment_row(row) is True


def test_positive_charge_is_never_payment():
    row = _row(amount=12.0, raw_description="PAYMENT THANK YOU")
    assert is_payment_row(row) is False
