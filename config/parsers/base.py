"""Shared helpers for statement parsers."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import pandas as pd

RAW_COLUMNS = [
    "posted_date",
    "amount",
    "raw_description",
    "card",
    "card_issuer",
    "card_product",
    "cardholder",
    "source_file",
]


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=RAW_COLUMNS)


def _is_missing(value: Any) -> bool:
    return value is None or (not isinstance(value, (list, tuple, dict)) and bool(pd.isna(value)))


def _is_blank_holder(value: Any) -> bool:
    if _is_missing(value):
        return True
    text = str(value).strip()
    return not text or text.lower() in {"nan", "none"}


def coerce_optional_amount(value: Any) -> float | None:
    if _is_missing(value) or (isinstance(value, str) and not value.strip()):
        return None
    return coerce_amount(value)


def coerce_amount(value: Any) -> float:
    """Parse a monetary value strictly; invalid values must never become zero."""
    if _is_missing(value) or (isinstance(value, str) and not value.strip()):
        raise ValueError("amount is blank")
    text = str(value).strip().replace("$", "").replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if text.endswith("-"):
        text = "-" + text[:-1]
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid amount {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"invalid amount {value!r}")
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def coerce_date(value: Any) -> datetime.date:
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "date") and callable(value.date) and not isinstance(value, str):
        return value.date()  # type: ignore[no-any-return]
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"Unparseable date: {value!r}")
    return parsed.date()


_MONTH_DAY_NUMERIC = re.compile(r"^(\d{1,2})/(\d{1,2})$")


def parse_month_day(value: str) -> tuple[int, int] | None:
    """Return (month, day) without using strptime's non-leap year 1900 default.

    ``datetime.strptime("02/29", "%m/%d")`` fails because it fills in 1900.
    Callers then resolve the year against the statement cycle, which may be a
    leap year such as 2024.
    """
    text = " ".join(str(value or "").split())
    numeric = _MONTH_DAY_NUMERIC.fullmatch(text)
    if numeric:
        month, day = int(numeric.group(1)), int(numeric.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return month, day
        return None
    try:
        parsed = datetime.strptime(f"{text} 2024", "%b %d %Y")
    except ValueError:
        return None
    return parsed.month, parsed.day


def resolve_cycle_date(
    month: int,
    day: int,
    cycle_start: date,
    cycle_end: date,
    *,
    grace_days: int = 1,
) -> date | None:
    """Map a month/day onto a statement cycle, allowing a small grace window.

    Issuers occasionally print a transaction one day outside the labeled period.
    Dates that still fall outside that grace window return ``None`` so parsers
    can skip the row instead of failing the whole statement.
    """
    candidates: list[date] = []
    for year in range(cycle_start.year - 1, cycle_end.year + 2):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    matching = [candidate for candidate in candidates if cycle_start <= candidate <= cycle_end]
    if len(matching) == 1:
        return matching[0]
    if grace_days > 0:
        grace_start = cycle_start - timedelta(days=grace_days)
        grace_end = cycle_end + timedelta(days=grace_days)
        matching = [candidate for candidate in candidates if grace_start <= candidate <= grace_end]
        if len(matching) == 1:
            return matching[0]
    return None


def finalize(
    rows: list[dict],
    card: str,
    source_file: str,
    metadata: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Validate parsed rows and add statement-level metadata.

    Parsers may supply metadata per row (for example, a supplementary-card
    holder) or once for the whole statement through ``metadata``. Keeping these
    fields in the raw parser contract makes them available to every downstream
    ledger, review, and export stage.
    """
    if not rows:
        frame = empty_frame()
        return frame
    frame = pd.DataFrame(rows)
    frame["card"] = card
    metadata = metadata or {}
    fallback_holder = metadata.get("cardholder")
    for column in ("card_issuer", "card_product", "cardholder"):
        if column not in frame.columns:
            frame[column] = metadata.get(column)
        elif column == "cardholder" and fallback_holder:
            frame[column] = [
                fallback_holder if _is_blank_holder(value) else value for value in frame[column]
            ]
    frame["source_file"] = source_file
    try:
        frame["posted_date"] = frame["posted_date"].map(coerce_date)
        frame["amount"] = frame["amount"].map(coerce_amount)
    except ValueError as exc:
        raise ValueError(f"{source_file}: {exc}") from exc
    frame["raw_description"] = frame["raw_description"].astype(str).str.strip()
    return frame[RAW_COLUMNS]
