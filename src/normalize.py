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
    "source_document_id",
    "source_occurrence",
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


IDENTITY_COLUMNS = ["card", "posted_date", "amount", "raw_description"]


def _with_transaction_occurrences(raw: pd.DataFrame) -> pd.DataFrame:
    """Assign deterministic ids while reconciling overlapping documents.

    Identical transaction fingerprints are reconciled as a multiset. Each
    document may contribute its first, second, etc. occurrence of a fingerprint;
    the ledger keeps one row for each occurrence number across all documents.
    Thus repeated real purchases within one statement survive, while an
    overlapping export containing the same occurrences does not double-count.
    """
    frame = raw.copy()
    frame["raw_description"] = frame["raw_description"].astype(str).str.strip()
    if frame["amount"].isna().any():
        raise ValueError("Cannot normalize a transaction with a missing amount")
    if "source_document_id" not in frame.columns:
        frame["source_document_id"] = None
    if "source_file" not in frame.columns:
        frame["source_file"] = ""
    if "source_row" not in frame.columns:
        frame["source_row"] = range(len(frame))

    # A legacy input without a hash still has stable per-file occurrence semantics.
    frame["_document_key"] = frame["source_document_id"].fillna("").astype(str)
    frame.loc[frame["_document_key"] == "", "_document_key"] = (
        "legacy:" + frame.loc[frame["_document_key"] == "", "source_file"].astype(str)
    )
    frame["_source_row"] = pd.to_numeric(frame["source_row"], errors="coerce").fillna(0).astype(int)
    frame = frame.sort_values([*IDENTITY_COLUMNS, "_document_key", "_source_row"], kind="stable")
    frame["source_occurrence"] = frame.groupby([*IDENTITY_COLUMNS, "_document_key"]).cumcount()

    frame["txn_id"] = [
        make_txn_id(c, d, a, desc, occurrence)
        for c, d, a, desc, occurrence in zip(
            frame["card"],
            frame["posted_date"],
            frame["amount"],
            frame["raw_description"],
            frame["source_occurrence"],
            strict=True,
        )
    ]
    return frame.drop(columns=["_document_key", "_source_row"], errors="ignore")


def assign_transaction_ids(raw: pd.DataFrame) -> pd.DataFrame:
    """Return one logical transaction per fingerprint occurrence."""
    frame = _with_transaction_occurrences(raw)
    # Different documents may be overlapping snapshots of the same card account.
    # Keep the maximum per-document multiplicity for each immutable fingerprint.
    frame["_source_sort"] = frame["source_document_id"].fillna("").astype(str)
    frame = frame.sort_values([*IDENTITY_COLUMNS, "source_occurrence", "_source_sort", "source_row"], kind="stable")
    return frame.drop(columns="_source_sort").drop_duplicates(
        subset=[*IDENTITY_COLUMNS, "source_occurrence"], keep="first"
    )


def transaction_sources(raw: pd.DataFrame) -> pd.DataFrame:
    """Return the document-to-logical-transaction provenance mapping."""
    frame = _with_transaction_occurrences(raw)
    columns = ["txn_id", "source_document_id", "source_file", "source_row", "source_occurrence"]
    return frame[columns].drop_duplicates().reset_index(drop=True)


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return empty_ledger()

    frame = assign_transaction_ids(raw)
    frame["normalized_merchant"] = frame["raw_description"].map(normalize_merchant)

    for column in ("canonical_merchant", "merchant_source", "proposed_canonical"):
        if column not in frame.columns:
            frame[column] = None
    frame["merchant_source"] = frame["merchant_source"].fillna("none")

    frame = frame.drop_duplicates(subset=["txn_id"], keep="first").reset_index(drop=True)
    return frame[LEDGER_COLUMNS]
