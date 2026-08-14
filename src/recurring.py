"""Recurring-bill detection and reconciliation against expected_recurring.yaml."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml

from src import paths

STALE_DAYS = 45
SUBSCRIPTION_CATEGORY = "Subscriptions"

RECURRING_COLUMNS = [
    "normalized_merchant",
    "canonical_merchant",
    "occurrences",
    "avg_amount",
    "std_amount",
    "median_gap_days",
    "is_recurring",
    "category",
    "subcategory",
    "months",
    "last_seen",
    "last_amount",
    "prior_avg_amount",
    "amount_change",
    "amount_change_pct",
    "flags",
]


def _expected_path(path: Path | None = None) -> Path:
    return path if path is not None else paths.EXPECTED_RECURRING_PATH


def load_expected(path: Path | None = None) -> list[dict]:
    target = _expected_path(path)
    if not target.exists():
        return []
    with target.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return list(doc.get("bills") or [])


def save_expected(bills: list[dict], path: Path | None = None) -> None:
    from filelock import FileLock

    from src.atomic import atomic_write_text

    target = _expected_path(path)
    with FileLock(f"{target}.lock"):
        atomic_write_text(target, yaml.safe_dump({"bills": bills}, sort_keys=False, allow_unicode=True))


def _empty_recurring() -> pd.DataFrame:
    return pd.DataFrame(columns=RECURRING_COLUMNS)


def _inclusive_months(start, end) -> int:
    """Calendar months from first to last charge, always at least 1."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if pd.isna(start_ts) or pd.isna(end_ts):
        return 1
    if end_ts < start_ts:
        start_ts, end_ts = end_ts, start_ts
    return max(1, (end_ts.year * 12 + end_ts.month) - (start_ts.year * 12 + start_ts.month) + 1)


def _merchant_keys(frame: pd.DataFrame) -> pd.Series:
    normalized = frame["normalized_merchant"].fillna("").astype(str).str.strip()
    if "canonical_merchant" not in frame.columns:
        return normalized
    canonical = frame["canonical_merchant"].fillna("").astype(str).str.strip()
    return canonical.where(canonical != "", normalized)


def detect_recurring(ledger: pd.DataFrame, min_occurrences: int = 2) -> pd.DataFrame:
    """Group Subscriptions by canonical merchant; flag a hike when last price exceeds the average."""
    if ledger.empty:
        return _empty_recurring()

    frame = ledger.copy()
    frame["posted_date"] = pd.to_datetime(frame["posted_date"], errors="coerce")
    as_of = frame["posted_date"].max()
    spend = frame[frame["amount"] > 0].copy()
    if spend.empty or "category" not in spend.columns:
        return _empty_recurring()
    categories = spend["category"].fillna("").astype(str).str.strip()
    spend = spend[categories.str.casefold() == SUBSCRIPTION_CATEGORY.casefold()].copy()
    if spend.empty:
        return _empty_recurring()
    spend["merchant_key"] = _merchant_keys(spend)

    rows: list[dict] = []
    for merchant, group in spend.groupby("merchant_key"):
        if not merchant or len(group) < min_occurrences:
            continue
        ordered = group.sort_values("posted_date")
        amounts = ordered["amount"].astype(float)
        dates = ordered["posted_date"]
        gaps = dates.diff().dt.days.dropna()
        median_gap = float(gaps.median()) if not gaps.empty else None
        std = float(amounts.std(ddof=0)) if len(amounts) > 1 else 0.0
        avg = float(amounts.mean())
        amount_stable = (std / avg) < 0.25 if avg else False
        monthlyish = median_gap is not None and 25 <= median_gap <= 40
        is_recurring = bool(amount_stable and (monthlyish or len(group) >= 3))
        top_cat = ordered["category"].dropna().mode()
        top_sub = ordered["subcategory"].dropna().mode()
        last_amount = float(amounts.iloc[-1])
        last_seen_ts = dates.iloc[-1]
        last_seen = last_seen_ts.date().isoformat() if not pd.isna(last_seen_ts) else None
        first_seen_ts = dates.dropna().iloc[0] if not dates.dropna().empty else last_seen_ts
        months = _inclusive_months(first_seen_ts, last_seen_ts)
        prior = amounts.iloc[:-1]
        prior_avg = float(prior.mean()) if len(prior) else None
        amount_change = round(last_amount - prior_avg, 2) if prior_avg is not None else None
        amount_change_pct = (
            round((last_amount - prior_avg) / prior_avg * 100, 1)
            if prior_avg not in (None, 0)
            else None
        )
        canonical = str(merchant)
        normalized = ordered["normalized_merchant"].dropna().astype(str).str.strip()
        normalized = normalized[normalized != ""]
        top_normalized = normalized.mode()
        has_canonical = (
            "canonical_merchant" in ordered.columns
            and ordered["canonical_merchant"].fillna("").astype(str).str.strip().eq(canonical).any()
        )
        flags: list[str] = []
        if round(avg, 2) < round(last_amount, 2):
            flags.append("price_hike")
        if is_recurring and not pd.isna(last_seen_ts) and not pd.isna(as_of):
            stale_days = int((as_of.normalize() - last_seen_ts.normalize()).days)
            if stale_days > STALE_DAYS:
                flags.append("stale")
        rows.append(
            {
                "normalized_merchant": (
                    top_normalized.iloc[0] if len(top_normalized) else canonical
                ),
                "canonical_merchant": canonical if has_canonical else None,
                "occurrences": int(len(group)),
                "avg_amount": round(avg, 2),
                "std_amount": round(std, 2),
                "median_gap_days": round(median_gap, 1) if median_gap is not None else None,
                "is_recurring": is_recurring,
                "category": top_cat.iloc[0] if len(top_cat) else SUBSCRIPTION_CATEGORY,
                "subcategory": top_sub.iloc[0] if len(top_sub) else None,
                "months": months,
                "last_seen": last_seen,
                "last_amount": round(last_amount, 2),
                "prior_avg_amount": round(prior_avg, 2) if prior_avg is not None else None,
                "amount_change": amount_change,
                "amount_change_pct": amount_change_pct,
                "flags": ",".join(flags),
            }
        )
    if not rows:
        return _empty_recurring()
    return pd.DataFrame(rows).sort_values(
        by=["is_recurring", "avg_amount"], ascending=[False, False]
    ).reset_index(drop=True)


def reconcile(ledger: pd.DataFrame, expected_path: Path | None = None) -> pd.DataFrame:
    bills = load_expected(expected_path)
    if ledger.empty:
        return pd.DataFrame(
            columns=["bill", "status", "expected_amount", "matched_merchant", "matched_avg", "last_seen", "last_amount"]
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
                    "last_amount": None,
                }
            )
            continue
        avg = float(matches["amount"].mean())
        latest = matches.sort_values("posted_date").iloc[-1]
        last_seen = pd.Timestamp(latest["posted_date"]).date().isoformat()
        last_amount = round(float(latest["amount"]), 2)
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
                "last_amount": last_amount,
            }
        )
    return pd.DataFrame(results)
