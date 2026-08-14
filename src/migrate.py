"""One-shot migration of ledgers written before txn_id was re-based.

Older ledgers hashed `normalized_merchant` into txn_id, so any change to
normalization invalidated every id. This recomputes ids from the immutable
source fields and carries classification decisions across.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.atomic import atomic_write_parquet
from src.normalize import CLASSIFICATION_COLUMNS, LEDGER_COLUMNS, assign_transaction_ids, normalize_merchant
from src import paths as path_config


def needs_migration(ledger: pd.DataFrame) -> bool:
    if ledger.empty:
        return False
    if not {"card", "posted_date", "amount", "raw_description"}.issubset(ledger.columns):
        return False
    expected = _recompute_ids(ledger)
    return not expected.equals(ledger["txn_id"].reset_index(drop=True))


def _recompute_ids(ledger: pd.DataFrame) -> pd.Series:
    return assign_transaction_ids(ledger.reset_index(drop=True))["txn_id"].reset_index(drop=True)


def migrate_ledger(ledger: pd.DataFrame) -> pd.DataFrame:
    """Return the ledger with new txn_ids and the full current column set."""
    if ledger.empty:
        return ledger

    out = ledger.copy().reset_index(drop=True)
    out = assign_transaction_ids(out)

    if "normalized_merchant" not in out.columns:
        out["normalized_merchant"] = out["raw_description"].map(normalize_merchant)

    for column in (
        "card_issuer",
        "card_product",
        "cardholder",
        "canonical_merchant",
        "merchant_source",
        "proposed_canonical",
        "source_document_id",
    ):
        if column not in out.columns:
            out[column] = None
    if "source_occurrence" not in out.columns:
        out["source_occurrence"] = 0
    out["merchant_source"] = out["merchant_source"].fillna("none")

    for column in CLASSIFICATION_COLUMNS:
        if column not in out.columns:
            out[column] = None

    out = out.drop_duplicates(subset=["txn_id"], keep="first").reset_index(drop=True)
    return out[LEDGER_COLUMNS + CLASSIFICATION_COLUMNS]


def migrate_file(path: Path | None = None) -> tuple[int, bool]:
    """Migrate the on-disk ledger in place. Returns (row_count, changed)."""
    target = path if path is not None else path_config.LEDGER_PARQUET
    if not target.exists():
        return 0, False
    ledger = pd.read_parquet(target)
    if ledger.empty:
        return 0, False

    changed = needs_migration(ledger) or not set(LEDGER_COLUMNS).issubset(ledger.columns)
    migrated = migrate_ledger(ledger)
    if changed:
        backup = target.with_suffix(".parquet.bak")
        atomic_write_parquet(ledger, backup)
        atomic_write_parquet(migrated, target)
    return len(migrated), changed
