"""Per-product statement coverage for the Cards page."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.cards import GAP_DAYS, STALE_DAYS, assign_cardholder, build_cards_coverage, product_in_use
from src.normalize import make_txn_id


def _row(**overrides):
    posted = overrides.get("posted_date", "2026-07-15")
    amount = overrides.get("amount", 12.0)
    raw = overrides.get("raw_description", "COFFEE SHOP")
    card = overrides.get("card", "chase-sapphire")
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
        "canonical_merchant": raw.title(),
        "merchant_source": "manual",
        "proposed_canonical": None,
        "source_file": "chase-sapphire/july.pdf",
        "source_document_id": "sapphire-july",
        "source_occurrence": 0,
        "category": "Food",
        "subcategory": None,
        "tags": [],
        "classified_by": "manual",
        "proposed_category": None,
        "proposed_subcategory": None,
    }
    base.update(overrides)
    base["txn_id"] = make_txn_id(base["card"], base["posted_date"], base["amount"], base["raw_description"])
    return base


def _by_label(payload: dict) -> dict[str, dict]:
    return {row["label"]: row for row in payload["products"]}


def test_continuous_monthly_spans_are_ok(monkeypatch):
    monkeypatch.setattr("src.cards.list_card_products", lambda: {"Chase": ["Sapphire Preferred"]})
    ledger = pd.DataFrame(
        [
            _row(posted_date="2026-06-02", source_document_id="s-jun", source_file="chase/jun.pdf"),
            _row(posted_date="2026-06-28", raw_description="JUNE DINNER", source_document_id="s-jun", source_file="chase/jun.pdf"),
            _row(posted_date="2026-07-01", raw_description="JULY COFFEE", source_document_id="s-jul", source_file="chase/jul.pdf"),
            _row(posted_date="2026-07-20", raw_description="JULY GROCERIES", source_document_id="s-jul", source_file="chase/jul.pdf"),
        ]
    )
    payload = build_cards_coverage(ledger, today=date(2026, 8, 1))
    sapphire = _by_label(payload)["Chase Sapphire Preferred · Alex Example"]
    assert sapphire["status"] == "ok"
    assert sapphire["statement_count"] == 2
    assert sapphire["gaps"] == []
    assert sapphire["stale_days"] is None


def test_skipped_month_between_documents_is_a_gap(monkeypatch):
    monkeypatch.setattr("src.cards.list_card_products", lambda: {})
    ledger = pd.DataFrame(
        [
            _row(
                card="chase-amazon",
                card_product="Amazon Prime Visa",
                posted_date="2026-01-05",
                source_document_id="amz-jan",
                source_file="chase-amazon/jan.pdf",
            ),
            _row(
                card="chase-amazon",
                card_product="Amazon Prime Visa",
                posted_date="2026-01-20",
                raw_description="JAN SHOP",
                source_document_id="amz-jan",
                source_file="chase-amazon/jan.pdf",
            ),
            _row(
                card="chase-amazon",
                card_product="Amazon Prime Visa",
                posted_date="2026-03-04",
                raw_description="MAR SHOP",
                source_document_id="amz-mar",
                source_file="chase-amazon/mar.pdf",
            ),
        ]
    )
    payload = build_cards_coverage(ledger, today=date(2026, 3, 10))
    amazon = _by_label(payload)["Chase Amazon Prime Visa · Alex Example"]
    assert amazon["status"] == "gap"
    assert amazon["gaps"] == [{"after": "2026-01-20", "before": "2026-03-04", "days": 43}]
    assert 43 > GAP_DAYS


def test_old_last_statement_is_stale(monkeypatch):
    monkeypatch.setattr("src.cards.list_card_products", lambda: {})
    last = date(2026, 5, 8)
    today = last + timedelta(days=STALE_DAYS + 5)
    ledger = pd.DataFrame(
        [
            _row(
                card_issuer="American Express",
                card_product="Platinum",
                posted_date=last.isoformat(),
                source_document_id="amex-may",
                source_file="amex/may.csv",
            )
        ]
    )
    payload = build_cards_coverage(ledger, today=today)
    platinum = _by_label(payload)["American Express Platinum · Alex Example"]
    assert platinum["status"] == "stale"
    assert platinum["stale_days"] == STALE_DAYS + 5
    assert platinum["gaps"] == []


def test_configured_product_with_no_ledger_rows_is_none(monkeypatch):
    monkeypatch.setattr(
        "src.cards.list_card_products",
        lambda: {"American Express": ["Platinum", "Delta Gold"]},
    )
    ledger = pd.DataFrame(
        [
            _row(
                card_issuer="American Express",
                card_product="Platinum",
                posted_date="2026-08-01",
                source_document_id="amex-aug",
                source_file="amex/aug.csv",
            )
        ]
    )
    payload = build_cards_coverage(ledger, today=date(2026, 8, 10))
    by_label = _by_label(payload)
    assert by_label["American Express Delta Gold"]["status"] == "none"
    assert by_label["American Express Delta Gold"]["statement_count"] == 0
    assert by_label["American Express Platinum · Alex Example"]["status"] == "ok"


def test_product_in_use_is_true_for_any_cardholder_on_that_product():
    ledger = pd.DataFrame(
        [
            _row(
                card_issuer="American Express",
                card_product="Platinum",
                cardholder="Alex Example",
            ),
            _row(
                card_issuer="American Express",
                card_product="Delta Gold",
                cardholder=None,
            ),
        ]
    )
    assert product_in_use(ledger, issuer="American Express", product="Platinum") is True
    assert product_in_use(ledger, issuer="American Express", product="Gold") is False
    assert product_in_use(pd.DataFrame(), issuer="American Express", product="Platinum") is False


def test_removed_vocab_product_no_longer_appears_as_empty_placeholder(monkeypatch):
    monkeypatch.setattr("src.cards.list_card_products", lambda: {"American Express": ["Platinum"]})
    ledger = pd.DataFrame(
        [
            _row(
                card_issuer="American Express",
                card_product="Platinum",
                posted_date="2026-08-01",
                source_document_id="amex-aug",
                source_file="amex/aug.csv",
            )
        ]
    )
    payload = build_cards_coverage(ledger, today=date(2026, 8, 10))
    labels = {row["label"] for row in payload["products"]}
    assert "American Express Gold" not in labels
    assert "American Express Delta Gold" not in labels
    assert "American Express Platinum · Alex Example" in labels


def test_payments_are_excluded_from_spend(monkeypatch):
    monkeypatch.setattr("src.cards.list_card_products", lambda: {})
    ledger = pd.DataFrame(
        [
            _row(amount=40.0, raw_description="GROCERIES"),
            _row(amount=-200.0, posted_date="2026-07-16", raw_description="PAYMENT THANK YOU"),
        ]
    )
    payload = build_cards_coverage(ledger, today=date(2026, 8, 1))
    sapphire = _by_label(payload)["Chase Sapphire Preferred · Alex Example"]
    assert sapphire["spend_total"] == 40.0
    assert sapphire["charge_count"] == 1
    assert sapphire["payments_total"] == 200.0
    assert sapphire["returns_total"] == 0.0
    assert sapphire["gross_charges"] == 40.0
    assert sapphire["account_kind"] == "card"
    assert sapphire["statements"][0]["spend_total"] == 40.0
    assert sapphire["statements"][0]["payments_total"] == 200.0
    assert sapphire["statements"][0]["returns_total"] == 0.0


def test_refund_reduces_net_and_splits_from_payments(monkeypatch):
    monkeypatch.setattr("src.cards.list_card_products", lambda: {})
    ledger = pd.DataFrame(
        [
            _row(amount=53.0, raw_description="AMAZON.COM", canonical_merchant="Amazon"),
            _row(
                amount=-14.0,
                posted_date="2026-07-16",
                raw_description="AMAZON.COM",
                canonical_merchant="Amazon",
            ),
            _row(
                amount=-200.0,
                posted_date="2026-07-17",
                raw_description="PAYMENT THANK YOU",
                category="Transfers",
                subcategory="Monthly Payment",
            ),
        ]
    )
    payload = build_cards_coverage(ledger, today=date(2026, 8, 1))
    sapphire = _by_label(payload)["Chase Sapphire Preferred · Alex Example"]
    assert sapphire["gross_charges"] == 53.0
    assert sapphire["returns_total"] == 14.0
    assert sapphire["payments_total"] == 200.0
    assert sapphire["spend_total"] == 39.0


def test_checking_product_is_bank_kind(monkeypatch):
    monkeypatch.setattr("src.cards.list_card_products", lambda: {"Bank of America": ["Advantage Checking"]})
    ledger = pd.DataFrame(
        [
            _row(
                card="boa-checking",
                card_issuer="Bank of America",
                card_product="Advantage Checking",
                posted_date="2026-07-02",
                amount=12.0,
                raw_description="GROCERIES",
                source_document_id="boa-jul",
                source_file="boa/jul.csv",
            )
        ]
    )
    payload = build_cards_coverage(ledger, today=date(2026, 8, 1))
    row = _by_label(payload)["Bank of America Advantage Checking · Alex Example"]
    assert row["account_kind"] == "bank"
    assert row["income_total"] == 0.0
    assert row["bank_expenses"] == 12.0
    assert row["spend_total"] == 12.0


def test_bank_income_and_card_funding_are_not_expenses(monkeypatch):
    monkeypatch.setattr("src.cards.list_card_products", lambda: {"Wells Fargo": ["Way2Save Savings"]})
    ledger = pd.DataFrame(
        [
            _row(
                card="wellsfargo-way2save-savings",
                card_issuer="Wells Fargo",
                card_product="Way2Save Savings",
                posted_date="2026-07-14",
                amount=-4692.05,
                raw_description="ACME PAYROLL",
                category="Income",
                subcategory="Payroll",
                source_document_id="wf-jul",
                source_file="wf/jul.csv",
            ),
            _row(
                card="wellsfargo-way2save-savings",
                card_issuer="Wells Fargo",
                card_product="Way2Save Savings",
                posted_date="2026-07-05",
                amount=2631.60,
                raw_description="AMERICAN EXPRESS ACH PMT",
                category="Transfers",
                subcategory="CardPayment",
                source_document_id="wf-jul",
                source_file="wf/jul.csv",
            ),
            _row(
                card="wellsfargo-way2save-savings",
                card_issuer="Wells Fargo",
                card_product="Way2Save Savings",
                posted_date="2026-07-04",
                amount=1834.56,
                raw_description="ROUNDPOINT MTG",
                category="Housing",
                subcategory="Mortgage",
                source_document_id="wf-jul",
                source_file="wf/jul.csv",
            ),
        ]
    )
    payload = build_cards_coverage(ledger, today=date(2026, 8, 1))
    row = _by_label(payload)["Wells Fargo Way2Save Savings · Alex Example"]
    assert row["account_kind"] == "bank"
    assert row["income_total"] == 4692.05
    assert row["bank_expenses"] == 1834.56
    assert row["spend_total"] == 1834.56
    assert row["payments_total"] == 0.0


def test_same_product_for_two_cardholders_stays_separate(monkeypatch):
    monkeypatch.setattr("src.cards.list_card_products", lambda: {"American Express": ["Delta Gold"]})
    ledger = pd.DataFrame(
        [
            _row(
                card="americanexpress-delta-gold",
                card_issuer="American Express",
                card_product="Delta Gold",
                cardholder="Alex Example",
                posted_date="2026-01-05",
                amount=40.0,
                raw_description="ALEX FLIGHT",
                source_document_id="alex-jan",
                source_file="amex/alex-jan.pdf",
            ),
            _row(
                card="americanexpress-delta-gold",
                card_issuer="American Express",
                card_product="Delta Gold",
                cardholder="Alex Example",
                posted_date="2026-03-04",
                amount=55.0,
                raw_description="ALEX HOTEL",
                source_document_id="alex-mar",
                source_file="amex/alex-mar.pdf",
            ),
            _row(
                card="americanexpress-delta-gold",
                card_issuer="American Express",
                card_product="Delta Gold",
                cardholder="Sam Example",
                posted_date="2026-03-01",
                amount=80.0,
                raw_description="SAM FLIGHT",
                source_document_id="sam-mar",
                source_file="amex/sam-mar.pdf",
            ),
            _row(
                card="americanexpress-delta-gold",
                card_issuer="American Express",
                card_product="Delta Gold",
                cardholder="Sam Example",
                posted_date="2026-03-08",
                amount=25.0,
                raw_description="SAM LOUNGE",
                source_document_id="sam-mar",
                source_file="amex/sam-mar.pdf",
            ),
        ]
    )
    payload = build_cards_coverage(ledger, today=date(2026, 3, 10))
    by_label = _by_label(payload)
    assert "American Express Delta Gold" not in by_label
    alex = by_label["American Express Delta Gold · Alex Example"]
    sam = by_label["American Express Delta Gold · Sam Example"]
    assert alex["spend_total"] == 95.0
    assert sam["spend_total"] == 105.0
    assert alex["status"] == "gap"
    assert sam["status"] == "ok"
    assert alex["gaps"] == [{"after": "2026-01-05", "before": "2026-03-04", "days": 58}]


def test_assign_cardholder_stamps_only_blank_rows_for_that_product(monkeypatch):
    monkeypatch.setattr("src.cards.list_card_products", lambda: {"American Express": ["Delta Gold"]})
    ledger = pd.DataFrame(
        [
            _row(
                card="americanexpress-delta-gold",
                card_issuer="American Express",
                card_product="Delta Gold",
                cardholder=None,
                posted_date="2026-01-05",
                amount=40.0,
                raw_description="FLIGHT",
                source_document_id="jan",
            ),
            _row(
                card="americanexpress-delta-gold",
                card_issuer="American Express",
                card_product="Delta Gold",
                cardholder="Sam Example",
                posted_date="2026-03-01",
                amount=80.0,
                raw_description="SAM FLIGHT",
                source_document_id="sam-mar",
            ),
            _row(
                card="americanexpress-platinum",
                card_issuer="American Express",
                card_product="Platinum",
                cardholder=None,
                posted_date="2026-03-04",
                amount=55.0,
                raw_description="PLAT DINNER",
                source_document_id="plat",
            ),
        ]
    )

    updated, txn_ids = assign_cardholder(
        ledger,
        issuer="American Express",
        product="Delta Gold",
        cardholder="Alex Example",
    )

    assert len(txn_ids) == 1
    assert updated.loc[updated["raw_description"] == "FLIGHT", "cardholder"].tolist() == ["Alex Example"]
    assert updated.loc[updated["raw_description"] == "SAM FLIGHT", "cardholder"].tolist() == ["Sam Example"]
    assert updated.loc[updated["raw_description"] == "PLAT DINNER", "cardholder"].isna().all()

    payload = build_cards_coverage(updated, today=date(2026, 3, 10))
    by_label = _by_label(payload)
    assert "American Express Delta Gold · Unassigned" not in by_label
    assert "American Express Delta Gold · Alex Example" in by_label
    assert "American Express Delta Gold · Sam Example" in by_label
