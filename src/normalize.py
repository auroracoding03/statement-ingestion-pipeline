"""Normalize merchant strings, assign stable txn_ids, and deduplicate.

Merchant identity has three layers:
  raw_description      immutable source text from the statement
  normalized_merchant  mechanical cleanup (this module)
  canonical_merchant   curated brand identity (src/merchants.py)

txn_id is hashed from the raw source text, never from a derived merchant field,
so re-tuning normalization or canonicalization does not churn transaction ids.
"""

from __future__ import annotations

import hashlib
import re

import pandas as pd

LEDGER_COLUMNS = [
    "txn_id",
    "card",
    "posted_date",
    "amount",
    "raw_description",
    "normalized_merchant",
    "canonical_merchant",
    "merchant_source",
    "proposed_canonical",
    "source_file",
]

CLASSIFICATION_COLUMNS = [
    "category",
    "subcategory",
    "classified_by",
    "proposed_category",
    "proposed_subcategory",
]

NOISE_RE = re.compile(
    r"(?:"
    r"\b\d{4,}\b"  # long number runs (auth codes, phones)
    r"|#\d+"
    r"|#\b"
    r"|\bstore\s*\d+\b"
    r"|\bloc(?:ation)?\s*\d+\b"
    r"|\busa\b|\bus\b"
    r")",
    re.IGNORECASE,
)
MULTI_SPACE = re.compile(r"\s+")


def normalize_merchant(raw: str) -> str:
    text = str(raw or "").upper().strip()
    text = text.replace("*", " ")
    text = NOISE_RE.sub(" ", text)
    text = MULTI_SPACE.sub(" ", text).strip()
    # Keep first ~6 tokens — enough for merchant identity, drops trailing city/state noise
    tokens = text.split()
    return " ".join(tokens[:6]) if tokens else "UNKNOWN"


def make_txn_id(card: str, posted_date, amount: float, raw_description: str, seq: int = 0) -> str:
    """Stable id anchored to immutable statement fields.

    `seq` disambiguates genuinely identical repeat purchases (same card, date,
    amount, and description) that would otherwise collapse into a single row.
    """
    key = f"{card}|{posted_date}|{float(amount):.2f}|{str(raw_description).strip()}|{seq}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=LEDGER_COLUMNS)


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return empty_ledger()

    frame = raw.copy()
    frame["raw_description"] = frame["raw_description"].astype(str).str.strip()
    frame["normalized_merchant"] = frame["raw_description"].map(normalize_merchant)

    # Ordinal within each identical (card, date, amount, description) group so that
    # true repeat purchases survive dedup while re-imports still collapse.
    frame["_seq"] = frame.groupby(
        ["card", "posted_date", "amount", "raw_description"]
    ).cumcount()

    frame["txn_id"] = [
        make_txn_id(c, d, a, desc, seq)
        for c, d, a, desc, seq in zip(
            frame["card"],
            frame["posted_date"],
            frame["amount"],
            frame["raw_description"],
            frame["_seq"],
            strict=True,
        )
    ]

    for column in ("canonical_merchant", "merchant_source", "proposed_canonical"):
        if column not in frame.columns:
            frame[column] = None
    frame["merchant_source"] = frame["merchant_source"].fillna("none")

    frame = frame.drop_duplicates(subset=["txn_id"], keep="first").reset_index(drop=True)
    return frame[LEDGER_COLUMNS]
