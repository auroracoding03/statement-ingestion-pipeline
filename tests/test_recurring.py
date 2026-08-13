"""Recurring detection flags: price hikes and stale bills."""

from datetime import timedelta

import pandas as pd

from src.normalize import make_txn_id
from src.recurring import detect_recurring, reconcile
import src.paths as paths


def _row(**overrides):
    posted = overrides.get("posted_date", "2026-07-15")
    amount = overrides.get("amount", 12.0)
    raw = overrides.get("raw_description", "NETFLIX")
    card = overrides.get("card", "chase")
    base = {
        "txn_id": make_txn_id(card, posted, amount, raw),
        "card": card,
        "posted_date": posted,
        "amount": amount,
        "raw_description": raw,
        "normalized_merchant": overrides.get("normalized_merchant", raw),
        "canonical_merchant": overrides.get("canonical_merchant", raw.title()),
        "category": overrides.get("category", "Subscriptions"),
        "subcategory": overrides.get("subcategory"),
    }
    base.update(overrides)
    base["txn_id"] = make_txn_id(base["card"], base["posted_date"], base["amount"], base["raw_description"])
    return base


def _monthly(merchant: str, amounts: list[float], last_date: str = "2026-07-15") -> pd.DataFrame:
    end = pd.Timestamp(last_date)
    rows = []
    for index, amount in enumerate(reversed(amounts)):
        posted = (end - timedelta(days=30 * index)).date().isoformat()
        rows.append(_row(posted_date=posted, amount=amount, raw_description=merchant, normalized_merchant=merchant))
    return pd.DataFrame(rows)


def test_stable_monthly_is_recurring_without_flags():
    ledger = _monthly("NETFLIX", [15.99, 15.99, 15.99, 15.99])
    detected = detect_recurring(ledger)
    row = detected.iloc[0]
    assert bool(row["is_recurring"]) is True
    assert row["flags"] == ""
    assert row["last_amount"] == 15.99


def test_price_hike_flags_when_last_charge_jumps():
    ledger = _monthly("NETFLIX", [11.99, 11.99, 11.99, 14.99])
    detected = detect_recurring(ledger)
    row = detected.iloc[0]
    assert "price_hike" in str(row["flags"]).split(",")
    assert row["last_amount"] == 14.99
    assert row["prior_avg_amount"] == 11.99


def test_stale_uses_ledger_as_of_not_wall_clock():
    old = _monthly("GYM", [40.0, 40.0, 40.0], last_date="2026-04-01")
    recent = pd.DataFrame([_row(posted_date="2026-07-15", amount=12.0, raw_description="COFFEE", normalized_merchant="COFFEE")])
    ledger = pd.concat([old, recent], ignore_index=True)
    detected = detect_recurring(ledger)
    gym = detected[detected["normalized_merchant"] == "GYM"].iloc[0]
    assert bool(gym["is_recurring"]) is True
    assert "stale" in str(gym["flags"]).split(",")


def test_one_off_merchant_is_not_stale():
    ledger = pd.DataFrame(
        [
            _row(posted_date="2026-01-01", amount=40.0, raw_description="GYM", normalized_merchant="GYM"),
            _row(posted_date="2026-07-15", amount=12.0, raw_description="COFFEE", normalized_merchant="COFFEE"),
            _row(posted_date="2026-07-16", amount=8.0, raw_description="COFFEE 2", normalized_merchant="COFFEE"),
        ]
    )
    detected = detect_recurring(ledger)
    assert "GYM" not in set(detected["normalized_merchant"])


def test_reconciliation_amount_mismatch_band(tmp_path, monkeypatch):
    expected = tmp_path / "expected_recurring.yaml"
    expected.write_text(
        "bills:\n  - name: Internet\n    merchant_regex: '(?i)comcast'\n    expected_amount: 80\n"
    )
    monkeypatch.setattr(paths, "EXPECTED_RECURRING_PATH", expected)
    ledger = pd.DataFrame(
        [
            _row(posted_date="2026-06-01", amount=120.0, raw_description="COMCAST", normalized_merchant="COMCAST"),
            _row(posted_date="2026-07-01", amount=120.0, raw_description="COMCAST", normalized_merchant="COMCAST"),
        ]
    )
    result = reconcile(ledger, expected_path=expected)
    assert result.iloc[0]["status"] == "amount_mismatch"
    assert result.iloc[0]["last_seen"] == "2026-07-01"
