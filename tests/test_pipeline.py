from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.classify import classify
from src.migrate import migrate_ledger, needs_migration
from src.normalize import make_txn_id, normalize, normalize_merchant
from src.recurring import detect_recurring


def test_normalize_merchant_strips_noise():
    assert "CHICK-FIL-A" in normalize_merchant("CHICK-FIL-A #01234 ATLANTA GA")
    assert "01234" not in normalize_merchant("CHICK-FIL-A #01234 ATLANTA GA")


def test_txn_id_stable():
    a = make_txn_id("chase", "2026-01-06", 12.47, "CHICK-FIL-A #1 ATLANTA GA")
    b = make_txn_id("chase", "2026-01-06", 12.47, "CHICK-FIL-A #1 ATLANTA GA")
    assert a == b
    assert len(a) == 16


def test_txn_id_survives_normalization_changes(monkeypatch):
    """The whole point of hashing raw text: retuning normalization must not churn ids."""
    raw = pd.DataFrame(
        [
            {
                "posted_date": "2026-01-06",
                "amount": 12.47,
                "raw_description": "CHICK-FIL-A #01234 ATLANTA GA",
                "card": "chase",
                "source_file": "x.csv",
            }
        ]
    )
    before = normalize(raw)["txn_id"].tolist()

    monkeypatch.setattr("src.normalize.normalize_merchant", lambda s: "COMPLETELY DIFFERENT")
    after = normalize(raw)["txn_id"].tolist()

    assert before == after


def test_identical_repeat_purchases_are_not_deduped():
    """Two identical same-day purchases are distinct transactions, not a re-import."""
    raw = pd.DataFrame(
        [
            {
                "posted_date": "2026-01-06",
                "amount": 4.25,
                "raw_description": "COFFEE CART",
                "card": "chase",
                "source_file": "x.csv",
            }
        ]
        * 2
    )
    out = normalize(raw)
    assert len(out) == 2
    assert out["txn_id"].nunique() == 2


def test_reimport_of_same_file_dedupes():
    row = {
        "posted_date": "2026-01-06",
        "amount": 4.25,
        "raw_description": "COFFEE CART",
        "card": "chase",
        "source_file": "x.csv",
    }
    first = normalize(pd.DataFrame([row]))
    second = normalize(pd.DataFrame([row]))
    assert first["txn_id"].tolist() == second["txn_id"].tolist()


def test_normalize_emits_merchant_columns():
    raw = pd.DataFrame(
        [
            {
                "posted_date": "2026-01-06",
                "amount": 12.47,
                "raw_description": "WAL-MART #1234",
                "card": "chase",
                "source_file": "x.csv",
            }
        ]
    )
    out = normalize(raw)
    for column in ("canonical_merchant", "merchant_source", "proposed_canonical"):
        assert column in out.columns
    assert out.iloc[0]["merchant_source"] == "none"


def test_classify_applies_rules(tmp_path: Path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "categories": ["Food"],
                "rules": [
                    {
                        "match": {"merchant_regex": "(?i)chick[- ]?fil[- ]?a"},
                        "category": "Food",
                        "subcategory": "FastFood",
                    }
                ],
            }
        )
    )
    frame = pd.DataFrame(
        [
            {
                "txn_id": "a",
                "card": "chase",
                "posted_date": "2026-01-06",
                "amount": 12.47,
                "raw_description": "CHICK-FIL-A #1",
                "normalized_merchant": "CHICK-FIL-A",
                "canonical_merchant": None,
                "source_file": "x",
            },
            {
                "txn_id": "b",
                "card": "chase",
                "posted_date": "2026-01-07",
                "amount": 6.75,
                "raw_description": "LOCAL COFFEE ROASTERS",
                "normalized_merchant": "LOCAL COFFEE ROASTERS",
                "canonical_merchant": None,
                "source_file": "x",
            },
        ]
    )
    out = classify(frame, rules_path=rules)
    chick = out[out["txn_id"] == "a"].iloc[0]
    assert chick["category"] == "Food"
    assert chick["classified_by"] == "rule"
    local = out[out["txn_id"] == "b"].iloc[0]
    assert local["classified_by"] is None or pd.isna(local["classified_by"])


def test_detect_recurring_flags_monthly():
    rows = []
    for month, day, amt in [(1, 20, 15.99), (2, 20, 15.99), (3, 20, 15.99)]:
        rows.append(
            {
                "txn_id": f"m{month}",
                "card": "chase",
                "posted_date": f"2026-{month:02d}-{day:02d}",
                "amount": amt,
                "raw_description": "NETFLIX.COM",
                "normalized_merchant": "NETFLIX.COM",
                "canonical_merchant": "Netflix",
                "category": "Subscriptions",
                "subcategory": "Streaming",
                "source_file": "x",
            }
        )
    rows.append(
        {
            "txn_id": "once",
            "card": "chase",
            "posted_date": "2026-01-25",
            "amount": 6.75,
            "raw_description": "LOCAL COFFEE",
            "normalized_merchant": "LOCAL COFFEE",
            "category": "Food",
            "subcategory": "",
            "source_file": "x",
        }
    )
    rows.append(
        {
            "txn_id": "m-house",
            "card": "chase",
            "posted_date": "2026-01-20",
            "amount": 2100.0,
            "raw_description": "ROCKET MORTGAGE AUTOPAY",
            "normalized_merchant": "ROCKET MORTGAGE AUTOPAY",
            "category": "Housing",
            "subcategory": "Mortgage",
            "source_file": "x",
        }
    )
    recurring = detect_recurring(pd.DataFrame(rows), min_occurrences=2)
    assert list(recurring["canonical_merchant"]) == ["Netflix"]
    rocket = recurring[recurring["normalized_merchant"] == "NETFLIX.COM"].iloc[0]
    assert bool(rocket["is_recurring"]) is True


def test_migrate_ledger_rebuilds_ids_and_keeps_categories():
    legacy = pd.DataFrame(
        [
            {
                "txn_id": "stale-id",
                "card": "chase",
                "posted_date": "2026-01-06",
                "amount": 12.47,
                "raw_description": "CHICK-FIL-A #01234",
                "normalized_merchant": "CHICK-FIL-A",
                "source_file": "x.csv",
                "category": "Food",
                "subcategory": "FastFood",
                "classified_by": "manual",
            }
        ]
    )
    assert needs_migration(legacy) is True

    migrated = migrate_ledger(legacy)
    assert migrated.iloc[0]["txn_id"] != "stale-id"
    assert migrated.iloc[0]["category"] == "Food"
    assert migrated.iloc[0]["classified_by"] == "manual"
    assert "canonical_merchant" in migrated.columns
    assert needs_migration(migrated) is False


@pytest.mark.parametrize("frame", [pd.DataFrame(), pd.DataFrame(columns=["txn_id"])])
def test_migrate_handles_empty(frame: pd.DataFrame):
    assert needs_migration(frame) is False
