"""Generic CSV parser — flexible header sniffing for unknown issuers."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import finalize

DATE_ALIASES = {"posted date", "post date", "transaction date", "date", "trans date"}
DESC_ALIASES = {"description", "memo", "payee", "merchant", "name", "details"}
AMOUNT_ALIASES = {"amount", "amt", "transaction amount", "debit"}


def _find_col(columns: list[str], aliases: set[str]) -> str | None:
    lowered = {c: c.strip().lower() for c in columns}
    for original, low in lowered.items():
        if low in aliases:
            return original
    return None


def parse_generic_csv(path: Path, card: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    date_col = _find_col(list(frame.columns), DATE_ALIASES)
    desc_col = _find_col(list(frame.columns), DESC_ALIASES)
    amount_col = _find_col(list(frame.columns), AMOUNT_ALIASES)

    if not date_col or not desc_col or not amount_col:
        raise ValueError(
            f"Could not map columns in {path}. Found: {list(frame.columns)}. "
            "Expected date/description/amount headers."
        )

    # Prefer Debit - Credit if both present
    credit_col = _find_col(list(frame.columns), {"credit"})
    debit_col = _find_col(list(frame.columns), {"debit"})
    rows: list[dict] = []
    for _, row in frame.iterrows():
        if debit_col and credit_col and amount_col.lower() not in {"amount", "amt", "transaction amount"}:
            debit = row.get(debit_col)
            credit = row.get(credit_col)
            amount = float(pd.to_numeric(debit, errors="coerce") or 0) - float(
                pd.to_numeric(credit, errors="coerce") or 0
            )
        else:
            amount = row[amount_col]
        desc = row[desc_col]
        if pd.isna(desc) or str(desc).strip() == "":
            continue
        rows.append(
            {
                "posted_date": row[date_col],
                "amount": amount,
                "raw_description": desc,
            }
        )
    return finalize(rows, card=card, source_file=str(path))
