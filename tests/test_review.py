"""Review clustering and rule preview."""

import pandas as pd

from src.classify import _compile_rules
from src.normalize import make_txn_id
from src.pipeline import open_matches_for_rule
from src.review import cluster_open_review, rule_from_row


def _row(**overrides):
    posted = overrides.get("posted_date", "2026-07-15")
    amount = overrides.get("amount", 12.0)
    raw = overrides.get("raw_description", "UBER TRIP")
    card = overrides.get("card", "chase")
    base = {
        "txn_id": make_txn_id(card, posted, amount, raw),
        "card": card,
        "posted_date": posted,
        "amount": amount,
        "raw_description": raw,
        "normalized_merchant": overrides.get("normalized_merchant", raw),
        "canonical_merchant": overrides.get("canonical_merchant"),
        "category": None,
        "subcategory": None,
        "classified_by": None,
        "proposed_category": overrides.get("proposed_category"),
        "proposed_subcategory": overrides.get("proposed_subcategory"),
    }
    base.update(overrides)
    base["txn_id"] = make_txn_id(base["card"], base["posted_date"], base["amount"], base["raw_description"])
    return base


def test_clusters_group_canonical_variants():
    ledger = pd.DataFrame(
        [
            _row(raw_description="UBER *TRIP", normalized_merchant="UBER TRIP", canonical_merchant="Uber", amount=18.0),
            _row(
                posted_date="2026-07-16",
                raw_description="UBER *TRIP 2",
                normalized_merchant="UBER TRIP 2",
                canonical_merchant="Uber",
                amount=22.0,
            ),
            _row(
                posted_date="2026-07-17",
                raw_description="LYFT RIDE",
                normalized_merchant="LYFT RIDE",
                canonical_merchant="Lyft",
                amount=14.0,
            ),
        ]
    )
    clusters = cluster_open_review(ledger)
    uber = next(item for item in clusters if item["merchant"] == "Uber")
    assert uber["count"] == 2
    assert uber["kind"] == "canonical"
    assert uber["representative_txn_id"] == make_txn_id("chase", "2026-07-16", 22.0, "UBER *TRIP 2")


def test_preview_count_matches_open_rows_and_does_not_need_compile_write():
    ledger = pd.DataFrame(
        [
            _row(raw_description="UBER *TRIP", normalized_merchant="UBER TRIP", canonical_merchant="Uber", amount=18.0),
            _row(
                posted_date="2026-07-16",
                raw_description="UBER *TRIP 2",
                normalized_merchant="UBER TRIP 2",
                canonical_merchant="Uber",
                amount=22.0,
            ),
        ]
    )
    spec = rule_from_row(ledger.iloc[0], category="Travel", subcategory="Rideshare", rule_scope="auto")
    assert spec is not None
    assert spec["match"]["merchant_canonical"] == "Uber"
    compiled = _compile_rules({"rules": [spec]})
    assert compiled
    matches = open_matches_for_rule(ledger, spec)
    assert {item["txn_id"] for item in matches} == set(ledger["txn_id"])
