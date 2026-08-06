"""Shared helpers for statement parsers."""

from __future__ import annotations

from datetime import datetime
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
    for column in ("card_issuer", "card_product", "cardholder"):
        if column not in frame.columns:
            frame[column] = metadata.get(column)
    frame["source_file"] = source_file
    try:
        frame["posted_date"] = frame["posted_date"].map(coerce_date)
        frame["amount"] = frame["amount"].map(coerce_amount)
    except ValueError as exc:
        raise ValueError(f"{source_file}: {exc}") from exc
    frame["raw_description"] = frame["raw_description"].astype(str).str.strip()
    return frame[RAW_COLUMNS]
