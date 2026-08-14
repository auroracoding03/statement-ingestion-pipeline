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
    assert row["last_seen"] == "2026-07-15"
    assert row["months"] == 4
    assert pd.notna(row["months"])


def test_same_month_charges_count_as_one_month():
    ledger = pd.DataFrame(
        [
            _row(posted_date="2026-07-02", amount=9.99, raw_description="SPOTIFY", normalized_merchant="SPOTIFY"),
            _row(posted_date="2026-07-20", amount=9.99, raw_description="SPOTIFY", normalized_merchant="SPOTIFY"),
        ]
    )
    detected = detect_recurring(ledger)
    row = detected.iloc[0]
    assert int(row["months"]) == 1
    assert row["avg_amount"] == 9.99
    assert row["last_amount"] == 9.99
    assert row["last_seen"] == "2026-07-20"


def test_price_hike_flags_when_last_charge_jumps():
    ledger = _monthly("NETFLIX", [11.99, 11.99, 11.99, 14.99])
    detected = detect_recurring(ledger)
    row = detected.iloc[0]
    assert "price_hike" in str(row["flags"]).split(",")
    assert row["last_amount"] == 14.99
    assert row["avg_amount"] == 12.74
    assert row["prior_avg_amount"] == 11.99


def test_price_hike_flags_when_average_is_below_last_price():
    ledger = _monthly("NETFLIX", [15.99, 15.99, 16.99])
    detected = detect_recurring(ledger)
    row = detected.iloc[0]
    assert row["last_amount"] == 16.99
    assert row["avg_amount"] < row["last_amount"]
    assert "price_hike" in str(row["flags"]).split(",")


def test_subscriptions_exclude_other_categories():
    housing = _monthly("ROCKET MORTGAGE", [2100.0, 2100.0, 2100.0])
    housing["category"] = "Housing"
    netflix = _monthly("NETFLIX", [15.99, 15.99, 15.99])
    detected = detect_recurring(pd.concat([housing, netflix], ignore_index=True))
    assert set(detected["normalized_merchant"]) == {"NETFLIX"}


def test_last_price_and_seen_use_canonical_latest_charge():
    ledger = pd.DataFrame(
        [
            _row(
                posted_date="2026-05-02",
                amount=9.99,
                raw_description="SPOTIFY COM",
                normalized_merchant="SPOTIFY COM",
                canonical_merchant="Spotify",
            ),
            _row(
                posted_date="2026-06-02",
                amount=9.99,
                raw_description="SPOTIFY USA",
                normalized_merchant="SPOTIFY USA",
                canonical_merchant="Spotify",
            ),
            _row(
                posted_date="2026-07-10",
                amount=11.99,
                raw_description="SPOTIFY PREMIUM",
                normalized_merchant="SPOTIFY PREMIUM",
                canonical_merchant="Spotify",
            ),
        ]
    )
    detected = detect_recurring(ledger)
    assert len(detected) == 1
    row = detected.iloc[0]
    assert row["canonical_merchant"] == "Spotify"
    assert row["last_amount"] == 11.99
    assert row["last_seen"] == "2026-07-10"
    assert "price_hike" in str(row["flags"]).split(",")


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
    assert result.iloc[0]["last_amount"] == 120.0
