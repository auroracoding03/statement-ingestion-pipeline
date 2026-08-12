"""Month overview summary for household spend conversations."""

from __future__ import annotations

import pandas as pd
import yaml

import src.paths as paths
from src.normalize import make_txn_id
from src.overview import LARGE_CHARGE_MIN, build_month_summary, cardholders


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
        "merchant_source": "manual",
        "proposed_canonical": None,
        "source_file": "chase/demo.csv",
        "source_document_id": "doc",
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


def _stub_overview_deps(monkeypatch, bills: str = "bills: []\n"):
    paths.EXPECTED_RECURRING_PATH.write_text(bills)
    monkeypatch.setattr("src.overview.list_tags", lambda: [])


def test_cardholders_are_sorted_unique_and_skip_blanks():
    ledger = pd.DataFrame(
        [
            _row(cardholder="Sam Example"),
            _row(posted_date="2026-07-16", raw_description="TEA", cardholder="Alex Example"),
            _row(posted_date="2026-07-17", raw_description="BLANK", cardholder=None),
            _row(posted_date="2026-07-18", raw_description="SAM2", cardholder="Sam Example"),
        ]
    )
    assert cardholders(ledger) == ["Alex Example", "Sam Example"]


def test_month_summary_excludes_payments_from_spend_and_reports_them(monkeypatch):
    _stub_overview_deps(monkeypatch)

    ledger = pd.DataFrame(
        [
            _row(posted_date="2026-07-02", amount=40.0, raw_description="GROCERIES", category="Food"),
            _row(posted_date="2026-07-03", amount=-200.0, raw_description="PAYMENT THANK YOU", category="Transfer"),
            _row(posted_date="2026-06-10", amount=25.0, raw_description="JUNE FOOD", category="Food"),
        ]
    )
    summary = build_month_summary(ledger, month="2026-07")

    assert summary["spend_total"] == 40.0
    assert summary["charge_count"] == 1
    assert summary["payments_and_refunds"] == 200.0
    assert summary["prior_spend_total"] == 25.0
    assert summary["spend_delta"] == 15.0


def test_cardholder_filter_and_unassigned_split(monkeypatch):
    _stub_overview_deps(monkeypatch)

    ledger = pd.DataFrame(
        [
            _row(amount=10.0, raw_description="ALEX FOOD", cardholder="Alex Example", category="Food"),
            _row(amount=30.0, raw_description="SAM FOOD", cardholder="Sam Example", category="Food"),
            _row(amount=5.0, raw_description="UNKNOWN FOOD", cardholder=None, category="Food"),
        ]
    )
    everyone = build_month_summary(ledger, month="2026-07")
    names = {row["name"]: row["total"] for row in everyone["holders"]}
    assert names["Alex Example"] == 10.0
    assert names["Sam Example"] == 30.0
    assert names["Unassigned"] == 5.0

    alex = build_month_summary(ledger, month="2026-07", cardholder="Alex Example")
    assert alex["spend_total"] == 10.0
    assert alex["holders"] == []
    assert alex["charge_count"] == 1


def test_large_charges_and_uncategorized_and_review(monkeypatch):
    _stub_overview_deps(monkeypatch)

    ledger = pd.DataFrame(
        [
            _row(amount=LARGE_CHARGE_MIN, raw_description="AIRLINE", category="Travel", classified_by="manual"),
            _row(amount=50.0, raw_description="SNACK", category=None, classified_by=None),
            _row(amount=12.0, raw_description="SMALL", category="Food", classified_by="manual"),
        ]
    )
    summary = build_month_summary(ledger, month="2026-07")
    assert [row["merchant"] for row in summary["large_charges"]] == ["Airline"]
    assert summary["uncategorized_count"] == 1
    assert summary["uncategorized_total"] == 50.0
    assert summary["review_count"] == 1


def test_tagged_trip_spend_and_bills_this_month(monkeypatch):
    paths.EXPECTED_RECURRING_PATH.write_text(
        yaml.safe_dump(
            {
                "bills": [
                    {"name": "Internet", "merchant_regex": "(?i)comcast"},
                    {"name": "Electric", "merchant_regex": "(?i)duke"},
                ]
            }
        )
    )
    monkeypatch.setattr(
        "src.overview.list_tags",
        lambda: [{"id": "beach", "label": "Beach trip", "kind": "trip"}],
    )

    ledger = pd.DataFrame(
        [
            _row(amount=80.0, raw_description="COMCAST", category="Utilities", tags=["beach"]),
            _row(amount=20.0, raw_description="HOTEL", category="Travel", tags=["beach"]),
        ]
    )
    summary = build_month_summary(ledger, month="2026-07")
    assert summary["tagged"] == [{"id": "beach", "label": "Beach trip", "kind": "trip", "total": 100.0}]
    bills = {row["bill"]: row["status"] for row in summary["bills"]}
    assert bills["Internet"] == "seen"
    assert bills["Electric"] == "missing"


def test_category_deltas_vs_prior_month(monkeypatch):
    _stub_overview_deps(monkeypatch)

    ledger = pd.DataFrame(
        [
            _row(posted_date="2026-06-08", amount=100.0, raw_description="JUNE FOOD", category="Food"),
            _row(posted_date="2026-07-08", amount=140.0, raw_description="JULY FOOD", category="Food"),
            _row(posted_date="2026-07-09", amount=60.0, raw_description="JULY GAS", category="Transport"),
        ]
    )
    summary = build_month_summary(ledger, month="2026-07")
    by_cat = {row["category"]: row for row in summary["categories"]}
    assert by_cat["Food"]["delta"] == 40.0
    assert by_cat["Transport"]["prior_total"] == 0.0
    assert by_cat["Transport"]["delta"] == 60.0
