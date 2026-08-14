"""Household spend summary for the Overview page."""

from __future__ import annotations

import re

import pandas as pd

from src import paths
from src.budget import budget_rows_for_period
from src.cashflow import charge_mask, non_payment_frame, summarize_spend
from src.periods import filter_posted, has_prior_history, month_keys, resolve_period
from src.recurring import load_expected
from src.review import needs_review
from src.tags import list_tags, normalize_tag_ids

LARGE_CHARGE_MIN = 200.0
LARGE_CHARGE_LIMIT = 10
UNASSIGNED = "Unassigned"
MONTH_PRESETS = ("month", "prev_month")


def _empty_summary(
    month: str | None = None,
    *,
    preset: str = "month",
    since: str | None = None,
    until: str | None = None,
    label: str | None = None,
    prior_since: str | None = None,
    prior_until: str | None = None,
) -> dict:
    return {
        "month": month,
        "months": [],
        "preset": preset,
        "since": since,
        "until": until,
        "prior_since": prior_since,
        "prior_until": prior_until,
        "label": label,
        "cardholder": None,
        "spend_total": 0.0,
        "prior_spend_total": None,
        "spend_delta": None,
        "spend_delta_pct": None,
        "charge_count": 0,
        "gross_charges": 0.0,
        "returns_total": 0.0,
        "payments_total": 0.0,
        "uncategorized_total": 0.0,
        "uncategorized_count": 0,
        "review_count": 0,
        "categories": [],
        "holders": [],
        "large_charges": [],
        "tagged": [],
        "bills": [],
        "budget_rows": [],
    }


def _filter_cardholder(frame: pd.DataFrame, cardholder: str | None) -> pd.DataFrame:
    if not cardholder:
        return frame
    if frame.empty or "cardholder" not in frame.columns:
        return frame.iloc[0:0].copy()
    names = frame["cardholder"].fillna("").astype(str).str.strip()
    return frame.loc[names == cardholder].copy()


def _is_uncategorized(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    text = str(value).strip()
    return text == "" or text == "Uncategorized"


def _money(value: float) -> float:
    return round(float(value), 2)


def _category_totals(spend: pd.DataFrame) -> dict[str, float]:
    if spend.empty:
        return {}
    cats = spend["category"].where(~spend["category"].map(_is_uncategorized), "Uncategorized")
    grouped = spend.assign(category=cats).groupby("category", dropna=False)["amount"].sum()
    return {str(name): _money(total) for name, total in grouped.items()}


def _merchant_label(row: pd.Series) -> str:
    canonical = row.get("canonical_merchant")
    if canonical is not None and not (isinstance(canonical, float) and pd.isna(canonical)) and str(canonical).strip():
        return str(canonical).strip()
    normalized = row.get("normalized_merchant")
    if normalized is not None and str(normalized).strip():
        return str(normalized).strip()
    return str(row.get("raw_description") or "").strip() or "Unknown"


def _bills_for_month(spend: pd.DataFrame) -> list[dict]:
    expected_path = paths.EXPECTED_RECURRING_PATH
    if not expected_path.exists():
        return []
    bills = load_expected(expected_path)
    if not bills:
        return []
    results: list[dict] = []
    for bill in bills:
        pattern = re.compile(bill.get("merchant_regex") or "(?!)")
        name = str(bill.get("name") or "Bill")
        if spend.empty:
            results.append({"bill": name, "status": "missing"})
            continue
        merchants = spend["normalized_merchant"].fillna("").astype(str)
        raw = spend["raw_description"].fillna("").astype(str)
        matched = spend[
            merchants.map(lambda text: bool(pattern.search(text)))
            | raw.map(lambda text: bool(pattern.search(text)))
        ]
        results.append({"bill": name, "status": "seen" if not matched.empty else "missing"})
    return results


def build_month_summary(
    ledger: pd.DataFrame,
    *,
    month: str | None = None,
    cardholder: str | None = None,
) -> dict:
    """Single-month summary. Kept as the Insights tool contract."""
    return build_period_summary(ledger, preset="month", month=month, cardholder=cardholder)


def build_period_summary(
    ledger: pd.DataFrame,
    *,
    preset: str = "month",
    month: str | None = None,
    since: str | None = None,
    until: str | None = None,
    cardholder: str | None = None,
) -> dict:
    months = month_keys(ledger)
    if not months:
        return _empty_summary(month, preset=preset, since=since, until=until)

    period = resolve_period(preset=preset, month=month, since=since, until=until, months=months)
    holder = cardholder.strip() if cardholder and cardholder.strip() else None
    scoped = _filter_cardholder(ledger, holder)
    current = filter_posted(scoped, period.since, period.until)
    prior = (
        filter_posted(scoped, period.prior_since, period.prior_until)
        if period.prior_since and period.prior_until
        else scoped.iloc[0:0].copy()
    )
    include_prior = has_prior_history(period, months)

    stats = summarize_spend(current)
    prior_stats = summarize_spend(prior)
    spend_total = stats["net_spend"]
    prior_total = prior_stats["net_spend"]
    charges = current.loc[charge_mask(current)] if not current.empty else current
    spend = non_payment_frame(current)
    prior_spend_rows = non_payment_frame(prior) if include_prior else prior.iloc[0:0].copy()

    if not include_prior:
        prior_spend = None
        delta = None
        delta_pct = None
    else:
        prior_spend = prior_total
        delta = _money(spend_total - prior_total)
        delta_pct = None if prior_total == 0 else round((spend_total - prior_total) / prior_total * 100, 1)

    current_cats = _category_totals(spend)
    prior_cats = _category_totals(prior_spend_rows) if include_prior else {}
    names = sorted(set(current_cats) | set(prior_cats), key=lambda name: (-current_cats.get(name, 0.0), name))
    categories = [
        {
            "category": name,
            "total": current_cats.get(name, 0.0),
            "prior_total": prior_cats.get(name, 0.0) if include_prior else None,
            "delta": _money(current_cats.get(name, 0.0) - prior_cats.get(name, 0.0)) if include_prior else None,
        }
        for name in names
    ]

    holders: list[dict] = []
    if holder is None and not spend.empty and "cardholder" in spend.columns:
        labels = spend["cardholder"].fillna("").astype(str).str.strip().replace("", UNASSIGNED)
        grouped = spend.assign(_holder=labels).groupby("_holder")["amount"].sum().sort_values(ascending=False)
        holders = [{"name": str(name), "total": _money(total)} for name, total in grouped.items()]

    large_charges: list[dict] = []
    if not charges.empty:
        big = charges[charges["amount"] >= LARGE_CHARGE_MIN].sort_values("amount", ascending=False).head(LARGE_CHARGE_LIMIT)
        for _, row in big.iterrows():
            posted = pd.to_datetime(row["posted_date"], errors="coerce")
            holder_name = row.get("cardholder")
            if holder_name is None or (isinstance(holder_name, float) and pd.isna(holder_name)) or not str(holder_name).strip():
                holder_name = None
            else:
                holder_name = str(holder_name).strip()
            category = row.get("category")
            large_charges.append(
                {
                    "posted_date": posted.date().isoformat() if not pd.isna(posted) else None,
                    "merchant": _merchant_label(row),
                    "amount": _money(row["amount"]),
                    "category": None if _is_uncategorized(category) else str(category),
                    "cardholder": holder_name,
                }
            )

    tagged: list[dict] = []
    vocab = {item["id"]: item for item in list_tags()}
    if not spend.empty and "tags" in spend.columns:
        rolls: dict[str, float] = {}
        for _, row in spend.iterrows():
            for tag_id in normalize_tag_ids(row.get("tags")):
                info = vocab.get(tag_id)
                if not info or info["kind"] not in {"trip", "occasion"}:
                    continue
                rolls[tag_id] = rolls.get(tag_id, 0.0) + float(row["amount"])
        tagged = [
            {
                "id": tag_id,
                "label": vocab[tag_id]["label"],
                "kind": vocab[tag_id]["kind"],
                "total": _money(total),
            }
            for tag_id, total in sorted(rolls.items(), key=lambda item: -item[1])
        ]

    uncategorized = charges[charges["category"].map(_is_uncategorized)] if not charges.empty else charges
    review_count = 0
    if not current.empty:
        review_count = int(current.apply(needs_review, axis=1).sum())

    bills: list[dict] = []
    if period.preset in MONTH_PRESETS:
        household = filter_posted(ledger, period.since, period.until)
        household_charges = household.loc[charge_mask(household)] if not household.empty else household
        bills = _bills_for_month(household_charges)

    return {
        "month": period.month,
        "months": months,
        "preset": period.preset,
        "since": period.since,
        "until": period.until,
        "prior_since": period.prior_since,
        "prior_until": period.prior_until,
        "label": period.label,
        "cardholder": holder,
        "spend_total": spend_total,
        "gross_charges": stats["gross_charges"],
        "prior_spend_total": prior_spend,
        "spend_delta": delta,
        "spend_delta_pct": delta_pct,
        "charge_count": stats["charge_count"],
        "returns_total": stats["returns_total"],
        "payments_total": stats["payments_total"],
        "uncategorized_total": _money(uncategorized["amount"].sum()) if not uncategorized.empty else 0.0,
        "uncategorized_count": int(len(uncategorized)),
        "review_count": review_count,
        "categories": categories,
        "holders": holders,
        "large_charges": large_charges,
        "tagged": tagged,
        "bills": bills,
        "budget_rows": budget_rows_for_period(spend, period),
    }


def cardholders(ledger: pd.DataFrame) -> list[str]:
    if ledger.empty or "cardholder" not in ledger.columns:
        return []
    names = ledger["cardholder"].dropna().astype(str).str.strip()
    return sorted({name for name in names if name})
