"""Charge vs monthly payment vs merchant return classification."""

from __future__ import annotations

import pandas as pd

from src.cashflow import household_spend_frame, is_payment_row, summarize_household, summarize_spend
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


def _bank(**overrides):
    return _row(
        card="wellsfargo-everyday-checking",
        card_issuer="Wells Fargo",
        card_product="Everyday Checking",
        **overrides,
    )


def test_bank_interest_is_not_a_card_payment():
    row = _bank(amount=-0.05, raw_description="INTEREST PAYMENT", category="Income", subcategory="Interest")
    assert is_payment_row(row) is False


def test_household_surplus_excludes_transfers_and_counts_income():
    grocery = _row(amount=40.0, raw_description="KROGER", category="Food", subcategory="Groceries")
    hoa = _bank(
        amount=388.0,
        raw_description="BILL PAY Example HOA, Inc ON-LINE",
        category="Housing",
        subcategory="HOA",
        posted_date="2026-07-04",
    )
    payroll = _bank(
        amount=-4692.05,
        raw_description="ACME CORP PAYROLL XXXXX1234",
        category="Income",
        subcategory="Payroll",
        posted_date="2026-07-14",
    )
    amex = _bank(
        amount=2631.60,
        raw_description="AMERICAN EXPRESS ACH PMT 260101 W9999",
        category="Transfers",
        subcategory="CardPayment",
        posted_date="2026-07-05",
    )
    sweep = _bank(
        amount=700.0,
        raw_description="ONLINE TRANSFER TO ALEX A EVERYDAY CHECKING",
        category="Transfers",
        subcategory="InternalTransfer",
        posted_date="2026-07-03",
    )
    amazon = _row(amount=53.0, raw_description="AMAZON.COM", canonical_merchant="Amazon", category="Shopping")
    refund = _row(
        amount=-14.0,
        posted_date="2026-07-16",
        raw_description="AMAZON.COM",
        canonical_merchant="Amazon",
        category="Shopping",
    )
    card_pay = _row(
        amount=-200.0,
        posted_date="2026-07-17",
        raw_description="PAYMENT THANK YOU",
        category="Transfers",
        subcategory="Monthly Payment",
    )
    frame = pd.DataFrame([grocery, hoa, payroll, amex, sweep, amazon, refund, card_pay])
    spend = household_spend_frame(frame)
    assert sorted(spend["raw_description"].tolist()) == sorted(
        ["AMAZON.COM", "AMAZON.COM", "KROGER", hoa["raw_description"]]
    )
    stats = summarize_household(frame)
    assert stats["gross_charges"] == 40.0 + 388.0 + 53.0
    assert stats["returns_total"] == 14.0
    assert stats["net_spend"] == 467.0
    assert stats["payments_total"] == 200.0
    assert stats["income_total"] == 4692.05
    assert stats["bank_expenses"] == 388.0
    assert stats["surplus"] == 4225.05


def test_unclassified_amex_ach_is_still_excluded_from_spend():
    row = _bank(amount=2631.60, raw_description="AMERICAN EXPRESS ACH PMT 260101", category=None, subcategory=None)
    stats = summarize_household(pd.DataFrame([row, _row(amount=12.0)]))
    assert stats["net_spend"] == 12.0
    assert stats["bank_expenses"] == 0.0


def test_bank_payee_rules_classify_transfers_and_income():
    from src.classify import classify

    frame = pd.DataFrame(
        [
            {
                "raw_description": "AMERICAN EXPRESS ACH PMT 260101 W9999",
                "normalized_merchant": "AMERICAN EXPRESS ACH PMT",
                "canonical_merchant": None,
            },
            {
                "raw_description": "ONLINE TRANSFER TO ALEX A EVERYDAY CHECKING",
                "normalized_merchant": "ONLINE TRANSFER TO ALEX A EVERYDAY CHECKING",
                "canonical_merchant": None,
            },
            {
                "raw_description": "ACME CORP PAYROLL XXXXX1234",
                "normalized_merchant": "ACME CORP PAYROLL",
                "canonical_merchant": None,
            },
            {
                "raw_description": "BILL PAY Example HOA, Inc ON-LINE",
                "normalized_merchant": "BILL PAY EXAMPLE HOA INC",
                "canonical_merchant": None,
            },
            {
                "raw_description": "INTEREST PAYMENT",
                "normalized_merchant": "INTEREST PAYMENT",
                "canonical_merchant": None,
            },
        ]
    )
    out = classify(frame)
    pairs = list(zip(out["category"], out["subcategory"], strict=True))
    assert pairs == [
        ("Transfers", "CardPayment"),
        ("Transfers", "InternalTransfer"),
        ("Income", "Payroll"),
        ("Housing", "HOA"),
        ("Income", "Interest"),
    ]
