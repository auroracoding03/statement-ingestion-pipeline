"""Recurring-bill detection and reconciliation against expected_recurring.yaml."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml

from src.paths import EXPECTED_RECURRING_PATH


def load_expected(path: Path = EXPECTED_RECURRING_PATH) -> list[dict]:
    with path.open() as f:
        doc = yaml.safe_load(f) or {}
    return list(doc.get("bills") or [])


def detect_recurring(ledger: pd.DataFrame, min_occurrences: int = 2) -> pd.DataFrame:
    """Group by normalized merchant; flag ~monthly, roughly-constant spend."""
    if ledger.empty:
        return pd.DataFrame(
            columns=[
                "normalized_merchant",
                "occurrences",
                "avg_amount",
                "std_amount",
                "median_gap_days",
                "is_recurring",
                "category",
                "subcategory",
            ]
        )

    frame = ledger.copy()
    frame["posted_date"] = pd.to_datetime(frame["posted_date"])
    # Focus on spend (positive amounts)
    spend = frame[frame["amount"] > 0].copy()

    rows: list[dict] = []
    for merchant, group in spend.groupby("normalized_merchant"):
        if len(group) < min_occurrences:
            continue
        amounts = group["amount"].astype(float)
        dates = group["posted_date"].sort_values()
        gaps = dates.diff().dt.days.dropna()
        median_gap = float(gaps.median()) if not gaps.empty else None
        std = float(amounts.std(ddof=0)) if len(amounts) > 1 else 0.0
        avg = float(amounts.mean())
        # ~monthly cadence: 25–35 day median gap, or at least 2 hits with low amount variance
        amount_stable = (std / avg) < 0.25 if avg else False
        monthlyish = median_gap is not None and 25 <= median_gap <= 40
        is_recurring = bool(amount_stable and (monthlyish or len(group) >= 3))
        top_cat = group["category"].dropna().mode()
        top_sub = group["subcategory"].dropna().mode()
        rows.append(
            {
                "normalized_merchant": merchant,
                "occurrences": int(len(group)),
                "avg_amount": round(avg, 2),
                "std_amount": round(std, 2),
                "median_gap_days": round(median_gap, 1) if median_gap is not None else None,
                "is_recurring": is_recurring,
                "category": top_cat.iloc[0] if len(top_cat) else None,
                "subcategory": top_sub.iloc[0] if len(top_sub) else None,
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "normalized_merchant",
                "occurrences",
                "avg_amount",
                "std_amount",
                "median_gap_days",
                "is_recurring",
                "category",
                "subcategory",
            ]
        )
    return pd.DataFrame(rows).sort_values(
        by=["is_recurring", "avg_amount"], ascending=[False, False]
    ).reset_index(drop=True)


def reconcile(ledger: pd.DataFrame, expected_path: Path = EXPECTED_RECURRING_PATH) -> pd.DataFrame:
    bills = load_expected(expected_path)
    if ledger.empty:
        return pd.DataFrame(
            columns=["bill", "status", "expected_amount", "matched_merchant", "matched_avg", "last_seen"]
        )

    spend = ledger[ledger["amount"] > 0].copy()
    spend["posted_date"] = pd.to_datetime(spend["posted_date"])
    results: list[dict] = []

    for bill in bills:
        pattern = re.compile(bill.get("merchant_regex") or "(?!)")
        expected_amount = bill.get("expected_amount")
        if expected_amount is not None and pd.isna(expected_amount):
            expected_amount = None
        matches = spend[
            spend["normalized_merchant"].astype(str).map(lambda m: bool(pattern.search(m)))
            | spend["raw_description"].astype(str).map(lambda m: bool(pattern.search(m)))
        ]
        if matches.empty:
            results.append(
                {
                    "bill": bill.get("name"),
                    "status": "missing",
                    "expected_amount": expected_amount,
                    "matched_merchant": None,
                    "matched_avg": None,
                    "last_seen": None,
                }
            )
            continue
        avg = float(matches["amount"].mean())
        last_seen = matches["posted_date"].max().date().isoformat()
        merchant = matches["normalized_merchant"].mode().iloc[0]
        status = "matched"
        if expected_amount is not None and abs(avg - float(expected_amount)) > max(
            5.0, 0.1 * float(expected_amount)
        ):
            status = "amount_mismatch"
        results.append(
            {
                "bill": bill.get("name"),
                "status": status,
                "expected_amount": expected_amount,
                "matched_merchant": merchant,
                "matched_avg": round(avg, 2),
                "last_seen": last_seen,
            }
        )
    return pd.DataFrame(results)
