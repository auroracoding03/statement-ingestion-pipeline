"""American Express CSV export parser."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .base import coerce_amount, finalize


def _cardholder(value: Any) -> str | None:
    if pd.isna(value) or not str(value).strip():
        return None
    text = " ".join(str(value).split())
    return text.title() if text.isupper() else text


def parse_amex_csv(path: Path, card: str, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    """Parse an Amex export using issuer/product selected at upload time."""
    metadata = dict(metadata or {})
    if metadata.get("card_issuer") != "American Express":
        raise ValueError("Amex CSV requires an American Express upload selection")
    if not metadata.get("card_product"):
        raise ValueError("Amex CSV requires a card product selected at upload")

    frame = pd.read_csv(path)
    # Common Amex headers: Date, Description, Amount  (or Extended Details)
    date_col = "Date" if "Date" in frame.columns else None
    desc_col = "Description" if "Description" in frame.columns else None
    amount_col = "Amount" if "Amount" in frame.columns else None
    if not date_col or not desc_col or not amount_col:
        raise ValueError(f"Amex CSV unexpected headers in {path}: {list(frame.columns)}")

    rows: list[dict] = []
    for _, row in frame.iterrows():
        desc = row[desc_col]
        if pd.isna(desc) or str(desc).strip() == "":
            continue
        amount = coerce_amount(row[amount_col])
        # Amex: charges positive; payments often negative — keep spend positive.
        if amount < 0:
            # payment / credit
            pass
        rows.append(
            {
                "posted_date": row[date_col],
                "amount": amount,
                "raw_description": desc,
                "cardholder": _cardholder(row.get("Card Member")),
            }
        )
    return finalize(rows, card=card, source_file=str(path), metadata=metadata)
