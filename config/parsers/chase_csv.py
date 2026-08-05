"""Chase credit card CSV export parser."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import finalize


def parse_chase_csv(path: Path, card: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    # Chase exports commonly: Transaction Date, Post Date, Description, Category, Type, Amount, Memo
    date_col = "Post Date" if "Post Date" in frame.columns else "Transaction Date"
    if date_col not in frame.columns:
        raise ValueError(f"Chase CSV missing date column: {path}")
    if "Description" not in frame.columns or "Amount" not in frame.columns:
        raise ValueError(f"Chase CSV missing Description/Amount: {path}")

    rows: list[dict] = []
    for _, row in frame.iterrows():
        desc = row["Description"]
        if pd.isna(desc) or str(desc).strip() == "":
            continue
        # Chase amounts: purchases are typically negative; flip so spend is positive.
        amount = float(row["Amount"])
        if amount < 0:
            amount = abs(amount)
        elif str(row.get("Type", "")).lower() in {"payment", "return", "adjustment"}:
            amount = -abs(amount)
        rows.append(
            {
                "posted_date": row[date_col],
                "amount": amount,
                "raw_description": desc,
            }
        )
    return finalize(rows, card=card, source_file=str(path))
