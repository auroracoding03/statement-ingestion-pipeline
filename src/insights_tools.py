"""Read-only Insights tools for budgets, tags, bills, and uncategorized spend."""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from src.budget import load_budget
from src.cashflow import charge_mask, household_spend_frame, summarize_spend
from src.periods import filter_posted, month_end, shift_month
from src.recurring import load_expected
from src.review import needs_review
from src.tags import list_tags, normalize_tag_ids


def _money(value: float) -> float:
    return round(float(value or 0), 2)


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return not str(value).strip() or str(value).strip().lower() in {"nan", "none"}


def _is_uncategorized(value) -> bool:
    if _blank(value):
        return True
    return str(value).strip() == "Uncategorized"


def _months_inclusive(start: date, end: date) -> int:
    if end < start:
        return 0
    return (end.year * 12 + end.month) - (start.year * 12 + start.month) + 1


def _shift_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(month=2, day=28, year=day.year + years)


def _month_start(day: date) -> date:
    return date(day.year, day.month, 1)


def _exact_category(frame: pd.DataFrame, category: str) -> pd.DataFrame:
    if frame.empty or "category" not in frame.columns:
        return frame.iloc[0:0].copy() if hasattr(frame, "iloc") else frame
    cats = frame["category"].fillna("").astype(str)
    return frame.loc[cats.str.casefold() == category.casefold()].copy()


def _exact_subcategory(frame: pd.DataFrame, subcategory: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    if "subcategory" not in frame.columns:
        return frame.iloc[0:0].copy()
    subs = frame["subcategory"].fillna("").astype(str)
    return frame.loc[subs.str.casefold() == subcategory.casefold()].copy()


def _cardholder(frame: pd.DataFrame, cardholder: str | None) -> pd.DataFrame:
    if not cardholder:
        return frame
    if frame.empty or "cardholder" not in frame.columns:
        return frame.iloc[0:0].copy()
    names = frame["cardholder"].fillna("").astype(str).str.strip()
    return frame.loc[names == cardholder].copy()


def _find_envelope(category: str, subcategory: str | None) -> tuple[str, str | None, float | None]:
    needle = category.casefold()
    for env in load_budget().get("envelopes") or []:
        name = str(env.get("category") or "").strip()
        if name.casefold() != needle:
            continue
        if subcategory:
            sub_needle = subcategory.casefold()
            for sub in env.get("subcategories") or []:
                sub_name = str(sub.get("subcategory") or "").strip()
                if sub_name.casefold() == sub_needle:
                    amount = sub.get("amount")
                    return name, sub_name, None if amount is None else _money(amount)
            return name, subcategory, None
        amount = env.get("amount")
        return name, None, None if amount is None else _money(amount)
    return category, subcategory, None


def _horizon(args: dict, today: date) -> tuple[date, date, date]:
    """Return (actual_since, actual_until, horizon_until)."""
    as_of = today
    months = args.get("months")
    if months:
        count = int(months)
        start = _month_start(as_of)
        until_month = shift_month(f"{start.year:04d}-{start.month:02d}", count - 1)
        horizon_until = month_end(until_month)
        actual_until = min(as_of, horizon_until)
        return start, actual_until, horizon_until
    year = as_of.year
    since = date.fromisoformat(args["since"]) if args.get("since") else date(year, 1, 1)
    horizon_until = date.fromisoformat(args["until"]) if args.get("until") else date(year, 12, 31)
    if horizon_until < since:
        horizon_until = since
    actual_until = min(as_of, horizon_until)
    if actual_until < since:
        actual_until = since
    return since, actual_until, horizon_until


def _category_net(frame: pd.DataFrame, *, category: str, subcategory: str | None, since: date, until: date, cardholder: str | None) -> dict:
    scoped = _cardholder(frame, cardholder)
    window = filter_posted(scoped, since.isoformat(), until.isoformat())
    matched = _exact_category(window, category)
    if subcategory:
        matched = _exact_subcategory(matched, subcategory)
    spend = household_spend_frame(matched)
    stats = summarize_spend(spend)
    return {
        "actual": stats["net_spend"],
        "charge_count": stats["charge_count"],
        "gross_charges": stats["gross_charges"],
        "returns_total": stats["returns_total"],
    }


def tool_remaining_budget(frame: pd.DataFrame, args: dict, today: date | None = None) -> dict:
    day = today or date.today()
    category = str(args["category"]).strip()
    subcategory = str(args["subcategory"]).strip() if args.get("subcategory") else None
    label, sub_label, monthly = _find_envelope(category, subcategory)
    since, actual_until, horizon_until = _horizon(args, day)
    spend = _category_net(
        frame,
        category=label,
        subcategory=sub_label,
        since=since,
        until=actual_until,
        cardholder=args.get("cardholder"),
    )
    months_in_horizon = _months_inclusive(since, horizon_until)
    elapsed_months = _months_inclusive(since, actual_until)
    remaining_months = _months_inclusive(_month_start(actual_until), horizon_until)
    budget_set = monthly is not None
    horizon_budget = _money(monthly * months_in_horizon) if budget_set else None
    remaining = _money(horizon_budget - spend["actual"]) if horizon_budget is not None else None
    remaining_per_month = (
        _money(remaining / remaining_months) if remaining is not None and remaining_months > 0 else None
    )
    straight_line = _money(monthly * elapsed_months) if budget_set else None
    pct_used = (
        round(spend["actual"] / horizon_budget * 100, 1)
        if horizon_budget not in (None, 0)
        else None
    )
    prior = _category_net(
        frame,
        category=label,
        subcategory=sub_label,
        since=_shift_years(since, -1),
        until=_shift_years(actual_until, -1),
        cardholder=args.get("cardholder"),
    )
    return {
        "category": label,
        "subcategory": sub_label,
        "budget_set": budget_set,
        "monthly_budget": monthly,
        "horizon_budget": horizon_budget,
        "actual": spend["actual"],
        "remaining": remaining,
        "remaining_per_month": remaining_per_month,
        "months_in_horizon": months_in_horizon,
        "remaining_months": remaining_months,
        "elapsed_months": elapsed_months,
        "straight_line_budget": straight_line,
        "pct_used": pct_used,
        "on_pace": spend["actual"] <= straight_line if straight_line is not None else None,
        "over_budget": spend["actual"] > horizon_budget if horizon_budget is not None else None,
        "calendar_year": (
            since.month == 1
            and since.day == 1
            and horizon_until.month == 12
            and horizon_until.day == 31
            and since.year == horizon_until.year
        ),
        "year": since.year,
        "charge_count": spend["charge_count"],
        "prior_year_actual": prior["actual"],
        "cardholder": args.get("cardholder"),
        "period": {
            "since": since.isoformat(),
            "until": actual_until.isoformat(),
            "horizon_until": horizon_until.isoformat(),
        },
    }


def _window_bounds(args: dict, today: date) -> tuple[date, date]:
    if args.get("month"):
        month = str(args["month"])
        start = date.fromisoformat(f"{month}-01")
        return start, month_end(month)
    since = date.fromisoformat(args["since"]) if args.get("since") else date(today.year, 1, 1)
    until = date.fromisoformat(args["until"]) if args.get("until") else today
    if until < since:
        until = since
    return since, until


def tool_budget_status(frame: pd.DataFrame, args: dict, today: date | None = None) -> dict:
    day = today or date.today()
    since, until = _window_bounds(args, day)
    factor = float(max(_months_inclusive(since, until), 1))
    scoped = _cardholder(frame, args.get("cardholder"))
    window = filter_posted(scoped, since.isoformat(), until.isoformat())
    spend = household_spend_frame(window)
    rows: list[dict] = []
    for env in load_budget().get("envelopes") or []:
        category = str(env.get("category") or "").strip()
        if not category:
            continue
        if env.get("amount") is not None:
            cat_frame = _exact_category(spend, category)
            actual = summarize_spend(cat_frame)["net_spend"]
            monthly = _money(env["amount"])
            window_budget = _money(monthly * factor)
            remaining = _money(window_budget - actual)
            over_by = _money(max(0.0, actual - window_budget))
            rows.append(
                {
                    "category": category,
                    "subcategory": None,
                    "monthly_budget": monthly,
                    "window_budget": window_budget,
                    "actual": actual,
                    "remaining": remaining,
                    "over_by": over_by,
                    "over_budget": actual > window_budget,
                }
            )
        for sub in env.get("subcategories") or []:
            if sub.get("amount") is None:
                continue
            sub_name = str(sub.get("subcategory") or "").strip()
            if not sub_name:
                continue
            sub_frame = _exact_subcategory(_exact_category(spend, category), sub_name)
            actual = summarize_spend(sub_frame)["net_spend"]
            monthly = _money(sub["amount"])
            window_budget = _money(monthly * factor)
            remaining = _money(window_budget - actual)
            over_by = _money(max(0.0, actual - window_budget))
            rows.append(
                {
                    "category": category,
                    "subcategory": sub_name,
                    "monthly_budget": monthly,
                    "window_budget": window_budget,
                    "actual": actual,
                    "remaining": remaining,
                    "over_by": over_by,
                    "over_budget": actual > window_budget,
                }
            )
    rows.sort(key=lambda row: (-int(row["over_budget"]), -float(row["actual"] - row["window_budget"]), row["category"]))
    over = [row for row in rows if row["over_budget"]]
    return {
        "rows": rows,
        "over_count": len(over),
        "envelope_count": len(rows),
        "cardholder": args.get("cardholder"),
        "period": {"since": since.isoformat(), "until": until.isoformat()},
    }


def tool_tagged_spend(frame: pd.DataFrame, args: dict, today: date | None = None) -> dict:
    del today
    needle = str(args.get("tag") or "").strip()
    vocab = list_tags()
    folded = needle.casefold()
    exact = [item for item in vocab if item["id"].casefold() == folded or item["label"].casefold() == folded]
    matches = exact or [
        item
        for item in vocab
        if folded in item["id"].casefold() or folded in item["label"].casefold()
    ]
    ids = {item["id"] for item in matches}
    scoped = _cardholder(frame, args.get("cardholder"))
    window = filter_posted(scoped, args.get("since"), args.get("until"))
    if window.empty or not ids or "tags" not in window.columns:
        matched = window.iloc[0:0].copy() if not window.empty else window
    else:
        mask = window["tags"].map(lambda value: bool(ids.intersection(normalize_tag_ids(value))))
        matched = window.loc[mask].copy()
    spend = household_spend_frame(matched)
    stats = summarize_spend(spend)
    return {
        "tag": needle,
        "matched_tags": [{"id": item["id"], "label": item["label"], "kind": item["kind"]} for item in matches],
        "ambiguous": len(matches) > 1,
        "gross_charges": stats["gross_charges"],
        "credits_refunds": stats["returns_total"],
        "net_spend": stats["net_spend"],
        "charge_count": stats["charge_count"],
        "cardholder": args.get("cardholder"),
        "period": {
            "since": args.get("since"),
            "until": args.get("until"),
        },
    }


def _bill_haystacks(frame: pd.DataFrame) -> pd.Series:
    parts = []
    for column in ("canonical_merchant", "normalized_merchant"):
        if column in frame.columns:
            parts.append(frame[column].fillna("").astype(str))
    if not parts:
        return pd.Series([""] * len(frame), index=frame.index)
    blob = parts[0]
    for extra in parts[1:]:
        blob = blob + " " + extra
    return blob


def tool_expected_bills(frame: pd.DataFrame, args: dict, today: date | None = None) -> dict:
    day = today or date.today()
    since, until = _window_bounds(args, day)
    bills = load_expected()
    name_filter = str(args["name"]).strip().casefold() if args.get("name") else None
    if name_filter:
        bills = [bill for bill in bills if name_filter in str(bill.get("name") or "").casefold()]
    scoped = filter_posted(frame, since.isoformat(), until.isoformat())
    charges = scoped.loc[charge_mask(scoped)] if not scoped.empty else scoped
    haystacks = _bill_haystacks(charges) if not charges.empty else pd.Series(dtype=str)
    rows: list[dict] = []
    for bill in bills:
        label = str(bill.get("name") or "Bill")
        try:
            pattern = re.compile(bill.get("merchant_regex") or "(?!)")
        except re.error:
            pattern = re.compile("(?!)")
        if charges.empty:
            matched = charges
        else:
            matched = charges.loc[haystacks.map(lambda text: bool(pattern.search(text)))]
        rows.append({"bill": label, "status": "seen" if not matched.empty else "missing"})
    seen = [row for row in rows if row["status"] == "seen"]
    missing = [row for row in rows if row["status"] == "missing"]
    return {
        "rows": rows,
        "seen_count": len(seen),
        "missing_count": len(missing),
        "period": {"since": since.isoformat(), "until": until.isoformat()},
    }


def tool_uncategorized_spend(frame: pd.DataFrame, args: dict, today: date | None = None) -> dict:
    day = today or date.today()
    since, until = _window_bounds(args, day)
    scoped = _cardholder(frame, args.get("cardholder"))
    window = filter_posted(scoped, since.isoformat(), until.isoformat())
    if window.empty:
        uncat = window
        review_count = 0
    else:
        cats = window["category"] if "category" in window.columns else pd.Series([None] * len(window), index=window.index)
        uncat = window.loc[cats.map(_is_uncategorized)].copy()
        review_count = int(window.apply(needs_review, axis=1).sum())
    spend = household_spend_frame(uncat)
    stats = summarize_spend(spend)
    return {
        "net_spend": stats["net_spend"],
        "gross_charges": stats["gross_charges"],
        "charge_count": stats["charge_count"],
        "txn_count": int(len(uncat)),
        "review_count": review_count,
        "cardholder": args.get("cardholder"),
        "period": {"since": since.isoformat(), "until": until.isoformat()},
    }
