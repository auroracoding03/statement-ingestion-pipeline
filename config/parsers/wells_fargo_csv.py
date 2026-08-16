"""Wells Fargo checking/savings account-history CSV parser."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .base import coerce_amount, finalize


def _col(frame: pd.DataFrame, *names: str) -> str | None:
    lowered = {str(column).strip().lower(): column for column in frame.columns}
    for name in names:
        if name in lowered:
            return lowered[name]
    return None


def parse_wells_fargo_csv(path: Path, card: str, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    frame = pd.read_csv(path)
    date_col = _col(frame, "date")
    desc_col = _col(frame, "description")
    amount_col = _col(frame, "amount")
    status_col = _col(frame, "status")
    if not date_col or not desc_col or not amount_col:
        raise ValueError(
            f"Wells Fargo account history CSV missing DATE/DESCRIPTION/AMOUNT columns in {path}. "
            f"Found: {list(frame.columns)}."
        )

    rows: list[dict] = []
    for _, row in frame.iterrows():
        desc = row[desc_col]
        if pd.isna(desc) or str(desc).strip() == "":
            continue
        if status_col:
            status = row[status_col]
            if not pd.isna(status) and str(status).strip() and str(status).strip().casefold() != "posted":
                continue
        # Native export: inflow positive, outflow negative. Ledger spend is positive.
        amount = -coerce_amount(row[amount_col])
        rows.append(
            {
                "posted_date": row[date_col],
                "amount": amount,
                "raw_description": desc,
            }
        )
    return finalize(rows, card=card, source_file=str(path), metadata=metadata)
