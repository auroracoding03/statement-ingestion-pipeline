"""The autouse data guard must never let tests touch the real ledger."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import src.paths as paths
import src.store as store


def test_default_ledger_write_stays_inside_tmp(tmp_path: Path):
    repo_ledger = Path(__file__).resolve().parents[1] / "data" / "ledger.parquet"
    before = repo_ledger.stat().st_mtime if repo_ledger.exists() else None
    before_size = repo_ledger.stat().st_size if repo_ledger.exists() else None

    written = store.write_ledger(pd.DataFrame([{"txn_id": "guard-test"}]))

    assert written == paths.LEDGER_PARQUET
    assert tmp_path.resolve() in paths.LEDGER_PARQUET.resolve().parents
    assert pd.read_parquet(written)["txn_id"].tolist() == ["guard-test"]
    if repo_ledger.exists():
        assert repo_ledger.stat().st_mtime == before
        assert repo_ledger.stat().st_size == before_size


def test_explicit_checkout_ledger_write_is_rejected():
    real = Path(__file__).resolve().parents[1] / "data" / "ledger.parquet"
    with pytest.raises(AssertionError, match="real data"):
        store.write_ledger(pd.DataFrame([{"txn_id": "nope"}]), real)
