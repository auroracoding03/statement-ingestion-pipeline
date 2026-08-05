"""One-shot migration of ledgers written before txn_id was re-based.

Older ledgers hashed `normalized_merchant` into txn_id, so any change to
normalization invalidated every id. This recomputes ids from the immutable
source fields and carries classification decisions across.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.normalize import CLASSIFICATION_COLUMNS, LEDGER_COLUMNS, make_txn_id, normalize_merchant
from src.paths import LEDGER_PARQUET


def needs_migration(ledger: pd.DataFrame) -> bool:
    if ledger.empty:
        return False
    if not {"card", "posted_date", "amount", "raw_description"}.issubset(ledger.columns):
        return False
    expected = _recompute_ids(ledger)
    return not expected.equals(ledger["txn_id"].reset_index(drop=True))


def _recompute_ids(ledger: pd.DataFrame) -> pd.Series:
    frame = ledger.copy().reset_index(drop=True)
    frame["raw_description"] = frame["raw_description"].astype(str).str.strip()
    seq = frame.groupby(["card", "posted_date", "amount", "raw_description"]).cumcount()
    return pd.Series(
        [
            make_txn_id(c, d, a, desc, s)
            for c, d, a, desc, s in zip(
                frame["card"],
                frame["posted_date"],
                frame["amount"],
                frame["raw_description"],
                seq,
                strict=True,
            )
        ]
    )


def migrate_ledger(ledger: pd.DataFrame) -> pd.DataFrame:
    """Return the ledger with new txn_ids and the full current column set."""
    if ledger.empty:
        return ledger

    out = ledger.copy().reset_index(drop=True)
    out["raw_description"] = out["raw_description"].astype(str).str.strip()
    out["txn_id"] = _recompute_ids(out)

    if "normalized_merchant" not in out.columns:
        out["normalized_merchant"] = out["raw_description"].map(normalize_merchant)

    for column in ("canonical_merchant", "merchant_source", "proposed_canonical"):
        if column not in out.columns:
            out[column] = None
    out["merchant_source"] = out["merchant_source"].fillna("none")

    for column in CLASSIFICATION_COLUMNS:
        if column not in out.columns:
            out[column] = None

    out = out.drop_duplicates(subset=["txn_id"], keep="first").reset_index(drop=True)
    return out[LEDGER_COLUMNS + CLASSIFICATION_COLUMNS]


def migrate_file(path: Path = LEDGER_PARQUET) -> tuple[int, bool]:
    """Migrate the on-disk ledger in place. Returns (row_count, changed)."""
    if not path.exists():
        return 0, False
    ledger = pd.read_parquet(path)
    if ledger.empty:
        return 0, False

    changed = needs_migration(ledger) or not set(LEDGER_COLUMNS).issubset(ledger.columns)
    migrated = migrate_ledger(ledger)
    if changed:
        backup = path.with_suffix(".parquet.bak")
        ledger.to_parquet(backup, index=False)
        migrated.to_parquet(path, index=False)
    return len(migrated), changed
