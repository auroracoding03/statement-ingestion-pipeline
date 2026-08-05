"""Generic CSV parser — flexible header sniffing for unknown issuers."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import coerce_optional_amount, finalize

DATE_ALIASES = {"posted date", "post date", "transaction date", "date", "trans date"}
DESC_ALIASES = {"description", "memo", "payee", "merchant", "name", "details"}
AMOUNT_ALIASES = {"amount", "amt", "transaction amount"}


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
    debit_col = _find_col(list(frame.columns), {"debit"})
    credit_col = _find_col(list(frame.columns), {"credit"})

    if not date_col or not desc_col or not (amount_col or debit_col or credit_col):
        raise ValueError(
            f"Could not map columns in {path}. Found: {list(frame.columns)}. "
            "Expected date/description/amount headers."
        )

    rows: list[dict] = []
    for row_number, (_, row) in enumerate(frame.iterrows(), start=2):
        desc = row[desc_col]
        if pd.isna(desc) or str(desc).strip() == "":
            continue
        if debit_col or credit_col:
            debit = coerce_optional_amount(row.get(debit_col)) if debit_col else None
            credit = coerce_optional_amount(row.get(credit_col)) if credit_col else None
            if debit is not None and credit is not None:
                raise ValueError(f"{path}: row {row_number} has both Debit and Credit values")
            if debit is None and credit is None:
                raise ValueError(f"{path}: row {row_number} has no Debit or Credit value")
            amount = abs(debit) if debit is not None else -abs(credit or 0)
        else:
            amount = row[amount_col]
        rows.append(
            {
                "posted_date": row[date_col],
                "amount": amount,
                "raw_description": desc,
            }
        )
    return finalize(rows, card=card, source_file=str(path))
