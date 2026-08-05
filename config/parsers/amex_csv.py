"""American Express CSV export parser."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import finalize


def parse_amex_csv(path: Path, card: str) -> pd.DataFrame:
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
        amount = float(str(row[amount_col]).replace(",", ""))
        # Amex: charges positive; payments often negative — keep spend positive.
        if amount < 0:
            # payment / credit
            pass
        rows.append(
            {
                "posted_date": row[date_col],
                "amount": amount,
                "raw_description": desc,
            }
        )
    return finalize(rows, card=card, source_file=str(path))
