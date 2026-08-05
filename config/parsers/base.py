"""Shared helpers for statement parsers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

RAW_COLUMNS = [
    "posted_date",
    "amount",
    "raw_description",
    "card",
    "source_file",
]


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=RAW_COLUMNS)


def coerce_amount(value: Any) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if text.endswith("-"):
        text = "-" + text[:-1]
    return float(text) if text else 0.0


def coerce_date(value: Any) -> datetime.date:
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "date") and callable(value.date) and not isinstance(value, str):
        return value.date()  # type: ignore[no-any-return]
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"Unparseable date: {value!r}")
    return parsed.date()


def finalize(rows: list[dict], card: str, source_file: str) -> pd.DataFrame:
    if not rows:
        frame = empty_frame()
        return frame
    frame = pd.DataFrame(rows)
    frame["card"] = card
    frame["source_file"] = source_file
    frame["posted_date"] = frame["posted_date"].map(coerce_date)
    frame["amount"] = frame["amount"].map(coerce_amount)
    frame["raw_description"] = frame["raw_description"].astype(str).str.strip()
    return frame[RAW_COLUMNS]
