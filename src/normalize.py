"""Normalize merchant strings, assign stable txn_ids, and deduplicate."""

from __future__ import annotations

import hashlib
import re

import pandas as pd

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


def make_txn_id(card: str, posted_date, amount: float, merchant: str) -> str:
    key = f"{card}|{posted_date}|{float(amount):.2f}|{merchant}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(
            columns=[
                "txn_id",
                "card",
                "posted_date",
                "amount",
                "raw_description",
                "normalized_merchant",
                "source_file",
            ]
        )

    frame = raw.copy()
    frame["normalized_merchant"] = frame["raw_description"].map(normalize_merchant)
    frame["txn_id"] = [
        make_txn_id(c, d, a, m)
        for c, d, a, m in zip(
            frame["card"],
            frame["posted_date"],
            frame["amount"],
            frame["normalized_merchant"],
            strict=True,
        )
    ]
    # Prefer first occurrence when re-importing overlapping statements
    frame = frame.drop_duplicates(subset=["txn_id"], keep="first").reset_index(drop=True)
    return frame[
        [
            "txn_id",
            "card",
            "posted_date",
            "amount",
            "raw_description",
            "normalized_merchant",
            "source_file",
        ]
    ]
