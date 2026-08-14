from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.store import last_statement_upload_at


def test_last_statement_upload_at_uses_latest_successful_ingest(tmp_path: Path) -> None:
    path = tmp_path / "ingestion_manifest.parquet"
    pd.DataFrame(
        [
            {"processed_at": "2026-01-01T10:00:00+00:00", "status": "parsed"},
            {"processed_at": "2026-03-15T18:30:00+00:00", "status": "failed"},
            {"processed_at": "2026-02-10T12:00:00+00:00", "status": "duplicate_document"},
        ]
    ).to_parquet(path)

    stamp = last_statement_upload_at(path)

    assert stamp is not None
    assert stamp.startswith("2026-02-10T12:00:00")


def test_last_statement_upload_at_missing_file(tmp_path: Path) -> None:
    assert last_statement_upload_at(tmp_path / "missing.parquet") is None
