"""Month overview summary for household spend conversations."""

from __future__ import annotations

import pandas as pd
import yaml

import src.paths as paths
from src.normalize import make_txn_id
from src.overview import LARGE_CHARGE_MIN, build_month_summary, build_period_summary, cardholders


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
    monkeypatch.setattr("src.budget.load_budget", lambda path=None: {"envelopes": []})


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
    assert summary["payments_total"] == 200.0
    assert summary["returns_total"] == 0.0
    assert summary["gross_charges"] == 40.0
    assert summary["prior_spend_total"] == 25.0
    assert summary["spend_delta"] == 15.0
    assert summary["income_total"] == 0.0
    assert summary["surplus"] == -40.0


def test_month_summary_nets_merchant_returns_not_payments(monkeypatch):
    _stub_overview_deps(monkeypatch)

    ledger = pd.DataFrame(
        [
            _row(posted_date="2026-07-02", amount=40.0, raw_description="GROCERIES", category="Food"),
            _row(
                posted_date="2026-07-04",
                amount=-10.0,
                raw_description="AMAZON.COM",
                canonical_merchant="Amazon",
                category="Shopping",
            ),
            _row(
                posted_date="2026-07-03",
                amount=-200.0,
                raw_description="PAYMENT THANK YOU",
                category="Transfers",
                subcategory="Monthly Payment",
            ),
        ]
    )
    summary = build_month_summary(ledger, month="2026-07")
    assert summary["gross_charges"] == 40.0
    assert summary["returns_total"] == 10.0
    assert summary["payments_total"] == 200.0
    assert summary["spend_total"] == 30.0
    assert summary["charge_count"] == 1
    assert summary["income_total"] == 0.0
    assert summary["surplus"] == -30.0


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


def test_t12m_sums_window_and_compares_prior_year(monkeypatch):
    _stub_overview_deps(monkeypatch)
    rows = []
    for month, amount in [
        ("2024-08", 10.0),
        ("2025-07", 15.0),
        ("2025-08", 40.0),
        ("2026-07", 60.0),
    ]:
        rows.append(
            _row(
                posted_date=f"{month}-15",
                amount=amount,
                raw_description=f"{month} FOOD",
                category="Food",
            )
        )
    rows.append(
        _row(posted_date="2026-07-20", amount=-50.0, raw_description="PAYMENT", category="Transfer")
    )
    summary = build_period_summary(
        pd.DataFrame(rows),
        preset="t12m",
        month="2026-07",
    )
    assert summary["since"] == "2025-08-01"
    assert summary["until"] == "2026-07-31"
    assert summary["spend_total"] == 100.0
    assert summary["prior_spend_total"] == 25.0
    assert summary["spend_delta"] == 75.0
    assert summary["bills"] == []
    assert summary["preset"] == "t12m"


def test_ytd_and_custom_are_inclusive(monkeypatch):
    _stub_overview_deps(monkeypatch)

    ledger = pd.DataFrame(
        [
            _row(posted_date="2025-01-15", amount=10.0, raw_description="LAST YTD", category="Food"),
            _row(posted_date="2026-01-01", amount=20.0, raw_description="JAN", category="Food"),
            _row(posted_date="2026-07-31", amount=30.0, raw_description="JULY END", category="Food"),
            _row(posted_date="2026-08-01", amount=99.0, raw_description="AUG", category="Food"),
        ]
    )
    ytd = build_period_summary(ledger, preset="ytd", month="2026-07")
    assert ytd["spend_total"] == 50.0
    assert ytd["since"] == "2026-01-01"
    assert ytd["until"] == "2026-07-31"
    assert ytd["prior_spend_total"] == 10.0

    custom = build_period_summary(
        ledger,
        preset="custom",
        since="2026-01-01",
        until="2026-07-31",
    )
    assert custom["spend_total"] == 50.0
    assert custom["since"] == "2026-01-01"


def test_period_summary_includes_shown_budget_rows(monkeypatch):
    _stub_overview_deps(monkeypatch)
    monkeypatch.setattr(
        "src.budget.load_budget",
        lambda path=None: {
            "envelopes": [
                {
                    "category": "Food",
                    "amount": 50.0,
                    "show_on_overview": True,
                    "subcategories": [
                        {"subcategory": "Groceries", "amount": 30.0, "show_on_overview": True},
                    ],
                }
            ]
        },
    )
    ledger = pd.DataFrame(
        [
            _row(amount=40.0, raw_description="GROCERIES", category="Food", subcategory="Groceries"),
            _row(amount=20.0, raw_description="DINNER", category="Food", subcategory="Restaurant"),
        ]
    )
    summary = build_period_summary(ledger, preset="month", month="2026-07")
    by_label = {row["label"]: row for row in summary["budget_rows"]}
    assert by_label["Food"]["actual"] == 60.0
    assert by_label["Food"]["budget"] == 50.0
    assert by_label["Food"]["variance"] == 10.0
    assert by_label["Food / Groceries"]["actual"] == 40.0
    assert by_label["Food / Groceries"]["variance"] == 10.0


def test_month_summary_reports_income_and_surplus(monkeypatch):
    _stub_overview_deps(monkeypatch)
    ledger = pd.DataFrame(
        [
            _row(posted_date="2026-07-02", amount=40.0, raw_description="GROCERIES", category="Food"),
            _row(
                posted_date="2026-07-04",
                amount=388.0,
                raw_description="EXAMPLE HOA",
                card="wellsfargo-everyday-checking",
                card_issuer="Wells Fargo",
                card_product="Everyday Checking",
                category="Housing",
                subcategory="HOA",
            ),
            _row(
                posted_date="2026-07-14",
                amount=-4692.05,
                raw_description="ACME PAYROLL",
                card="wellsfargo-way2save-savings",
                card_issuer="Wells Fargo",
                card_product="Way2Save Savings",
                category="Income",
                subcategory="Payroll",
            ),
            _row(
                posted_date="2026-07-05",
                amount=2631.60,
                raw_description="AMERICAN EXPRESS ACH PMT",
                card="wellsfargo-way2save-savings",
                card_issuer="Wells Fargo",
                card_product="Way2Save Savings",
                category="Transfers",
                subcategory="CardPayment",
            ),
            _row(
                posted_date="2026-07-03",
                amount=-200.0,
                raw_description="PAYMENT THANK YOU",
                category="Transfers",
                subcategory="Monthly Payment",
            ),
        ]
    )
    summary = build_month_summary(ledger, month="2026-07")
    assert summary["spend_total"] == 428.0
    assert summary["income_total"] == 4692.05
    assert summary["surplus"] == 4264.05
    assert summary["payments_total"] == 200.0
    categories = {row["category"]: row["total"] for row in summary["categories"]}
    assert "Transfers" not in categories
    assert "Income" not in categories
    assert categories["Housing"] == 388.0
    assert categories["Food"] == 40.0
