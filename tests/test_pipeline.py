from pathlib import Path

import pandas as pd

from src.normalize import make_txn_id, normalize, normalize_merchant
from src.classify import classify
from src.recurring import detect_recurring


def test_normalize_merchant_strips_noise():
    assert "CHICK-FIL-A" in normalize_merchant("CHICK-FIL-A #01234 ATLANTA GA")
    assert "01234" not in normalize_merchant("CHICK-FIL-A #01234 ATLANTA GA")


def test_txn_id_stable():
    a = make_txn_id("chase", "2026-01-06", 12.47, "CHICK FIL A")
    b = make_txn_id("chase", "2026-01-06", 12.47, "CHICK FIL A")
    assert a == b
    assert len(a) == 16


def test_classify_applies_rules(tmp_path: Path):
    frame = pd.DataFrame(
        [
            {
                "txn_id": "a",
                "card": "chase",
                "posted_date": "2026-01-06",
                "amount": 12.47,
                "raw_description": "CHICK-FIL-A #1",
                "normalized_merchant": "CHICK-FIL-A",
                "source_file": "x",
            },
            {
                "txn_id": "b",
                "card": "chase",
                "posted_date": "2026-01-07",
                "amount": 6.75,
                "raw_description": "LOCAL COFFEE ROASTERS",
                "normalized_merchant": "LOCAL COFFEE ROASTERS",
                "source_file": "x",
            },
        ]
    )
    out = classify(frame)
    chick = out[out["txn_id"] == "a"].iloc[0]
    assert chick["category"] == "Food"
    assert chick["classified_by"] == "rule"
    local = out[out["txn_id"] == "b"].iloc[0]
    assert local["classified_by"] is None or pd.isna(local["classified_by"])


def test_detect_recurring_flags_monthly():
    rows = []
    for month, day, amt in [(1, 20, 2100.0), (2, 20, 2100.0), (3, 20, 2100.0)]:
        rows.append(
            {
                "txn_id": f"m{month}",
                "card": "chase",
                "posted_date": f"2026-{month:02d}-{day:02d}",
                "amount": amt,
                "raw_description": "ROCKET MORTGAGE AUTOPAY",
                "normalized_merchant": "ROCKET MORTGAGE AUTOPAY",
                "category": "Housing",
                "subcategory": "Mortgage",
                "source_file": "x",
            }
        )
    # one-off
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
    recurring = detect_recurring(pd.DataFrame(rows), min_occurrences=2)
    rocket = recurring[recurring["normalized_merchant"] == "ROCKET MORTGAGE AUTOPAY"].iloc[0]
    assert bool(rocket["is_recurring"]) is True
