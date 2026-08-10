"""Regression coverage for ingestion correctness, privacy, and recovery."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

import src.pipeline as pipeline
import src.store as store
from config.parsers.generic_csv import parse_generic_csv
from src.extract import ExtractionError, extract_statements
from src.normalize import normalize, transaction_sources
from src.recurring import detect_recurring
from src.store import export_for_dashboard


def test_generic_debit_credit_keeps_signs_and_never_zeroes_blanks(tmp_path: Path):
    statement = tmp_path / "generic.csv"
    statement.write_text(
        "Date,Description,Debit,Credit\n"
        "2026-01-01,Coffee,10.00,\n"
        "2026-01-02,Refund,,5.00\n"
    )

    parsed = parse_generic_csv(statement, "generic")

    assert parsed["amount"].tolist() == [10.0, -5.0]


@pytest.mark.parametrize(
    "row",
    [
        "2026-01-01,Missing,,\n",
        "2026-01-01,Both,10.00,5.00\n",
    ],
)
def test_generic_debit_credit_rejects_ambiguous_or_blank_rows(tmp_path: Path, row: str):
    statement = tmp_path / "generic.csv"
    statement.write_text("Date,Description,Debit,Credit\n" + row)

    with pytest.raises(ValueError):
        parse_generic_csv(statement, "generic")


def test_overlapping_documents_use_maximum_occurrence_not_sum():
    raw = pd.DataFrame(
        [
            {
                "posted_date": "2026-01-06",
                "amount": 4.25,
                "raw_description": "COFFEE CART",
                "card": "chase",
                "source_file": "a.csv",
                "source_document_id": "a",
                "source_row": 0,
            },
            {
                "posted_date": "2026-01-06",
                "amount": 4.25,
                "raw_description": "COFFEE CART",
                "card": "chase",
                "source_file": "a.csv",
                "source_document_id": "a",
                "source_row": 1,
            },
            {
                "posted_date": "2026-01-06",
                "amount": 4.25,
                "raw_description": "COFFEE CART",
                "card": "chase",
                "source_file": "overlap.csv",
                "source_document_id": "b",
                "source_row": 0,
            },
        ]
    )

    ledger = normalize(raw)

    assert len(ledger) == 2
    assert ledger["source_occurrence"].tolist() == [0, 1]
    assert ledger["txn_id"].nunique() == 2
    links = transaction_sources(raw)
    assert len(links) == 3
    assert links["txn_id"].nunique() == 2


def test_identical_document_bytes_are_not_parsed_twice(tmp_path: Path):
    inbox = tmp_path / "inbox"
    card_dir = inbox / "generic"
    card_dir.mkdir(parents=True)
    content = "Date,Description,Amount\n2026-01-01,Coffee,10.00\n"
    (card_dir / "jan.csv").write_text(content)
    (card_dir / "copy.csv").write_text(content)

    result = extract_statements(inbox)

    assert len(result.frame) == 1
    assert set(result.manifest["status"]) == {"parsed", "duplicate_document"}


def test_underscore_inbox_dirs_are_ignored(tmp_path: Path):
    inbox = tmp_path / "inbox"
    (inbox / "generic").mkdir(parents=True)
    (inbox / "_quarantine").mkdir(parents=True)
    (inbox / "generic" / "ok.csv").write_text("Date,Description,Amount\n2026-01-01,Coffee,10.00\n")
    (inbox / "_quarantine" / "bad.csv").write_text("Date,Description\n2026-01-02,Broken\n")

    result = extract_statements(inbox)

    assert len(result.frame) == 1
    assert result.manifest["status"].tolist() == ["parsed"]
    assert Path(result.manifest.iloc[0]["source_file"]).as_posix() == "generic/ok.csv"


def test_parser_failure_does_not_replace_existing_ledger(tmp_path: Path, monkeypatch):
    inbox = tmp_path / "inbox"
    card_dir = inbox / "generic"
    card_dir.mkdir(parents=True)
    (card_dir / "valid.csv").write_text("Date,Description,Amount\n2026-01-01,Coffee,10.00\n")
    (card_dir / "broken.csv").write_text("Date,Description\n2026-01-02,Bad row\n")

    ledger_path = tmp_path / "ledger.parquet"
    lock_path = tmp_path / "ledger.lock"
    old = normalize(
        pd.DataFrame(
            [
                {
                    "posted_date": "2025-12-31",
                    "amount": 22.0,
                    "raw_description": "EXISTING",
                    "card": "chase",
                    "source_file": "old.csv",
                }
            ]
        )
    )
    old.to_parquet(ledger_path, index=False)
    before = ledger_path.read_bytes()
    manifests: list[pd.DataFrame] = []

    monkeypatch.setattr(pipeline, "INBOX", inbox)
    monkeypatch.setattr(pipeline, "LEDGER_PARQUET", ledger_path)
    monkeypatch.setattr(pipeline, "LEDGER_LOCK", lock_path)
    monkeypatch.setattr(pipeline, "ensure_dirs", lambda: None)
    monkeypatch.setattr(pipeline, "write_ingest_manifest", lambda frame: manifests.append(frame.copy()))

    result = pipeline.run_ingest()

    assert "error" in result
    assert ledger_path.read_bytes() == before
    assert manifests and "failed" in set(manifests[0]["status"])


def test_recurring_returns_empty_schema_for_singleton_merchants():
    ledger = pd.DataFrame(
        [
            {
                "posted_date": "2026-01-01",
                "amount": 10.0,
                "normalized_merchant": "ONE OFF",
                "category": None,
                "subcategory": None,
            }
        ]
    )

    recurring = detect_recurring(ledger)

    assert recurring.empty
    assert "is_recurring" in recurring.columns


def test_ledger_write_keeps_a_last_known_good_backup(tmp_path: Path, monkeypatch):
    path = tmp_path / "ledger.parquet"
    monkeypatch.setattr(store, "ensure_dirs", lambda: None)
    store.write_ledger(pd.DataFrame([{"txn_id": "old"}]), path)
    store.write_ledger(pd.DataFrame([{"txn_id": "new"}]), path)

    assert pd.read_parquet(path)["txn_id"].tolist() == ["new"]
    assert pd.read_parquet(path.with_suffix(".parquet.bak"))["txn_id"].tolist() == ["old"]


def test_aggregate_export_never_contains_transaction_rows_or_stale_full_files(tmp_path: Path, monkeypatch):
    publish = tmp_path / "publish.yaml"
    export_dir = tmp_path / "export"
    ledger = pd.DataFrame(
        [
            {
                "txn_id": "secret-id",
                "card": "chase",
                "posted_date": "2026-01-01",
                "amount": 12.5,
                "raw_description": "PRIVATE COFFEE ORDER",
                "normalized_merchant": "PRIVATE COFFEE",
                "canonical_merchant": None,
                "merchant_source": "none",
                "proposed_canonical": None,
                "source_file": "/Users/me/private.csv",
                "source_document_id": "secret-doc",
                "source_occurrence": 0,
                "category": None,
                "subcategory": None,
                "classified_by": None,
                "proposed_category": None,
                "proposed_subcategory": None,
            }
        ]
    )
    recurring = pd.DataFrame(
        [
            {
                "normalized_merchant": "PRIVATE COFFEE",
                "occurrences": 2,
                "avg_amount": 12.5,
                "std_amount": 0.0,
                "median_gap_days": 30.0,
                "is_recurring": True,
                "category": "Food",
                "subcategory": "Coffee",
            }
        ]
    )
    reconciliation = pd.DataFrame(
        [
            {
                "bill": "Private bill",
                "status": "matched",
                "expected_amount": 12.5,
                "matched_merchant": "PRIVATE COFFEE",
                "matched_avg": 12.5,
                "last_seen": "2026-01-01",
            }
        ]
    )

    monkeypatch.setattr(store, "ensure_dirs", lambda: None)
    publish.write_text(yaml.safe_dump({"mode": "full", "include_merchant_names": True}))
    export_for_dashboard(ledger, recurring, reconciliation, publish, export_dir)
    assert (export_dir / "ledger.json").exists()

    publish.write_text(yaml.safe_dump({"mode": "aggregates_only", "include_merchant_names": False}))
    export_for_dashboard(ledger, recurring, reconciliation, publish, export_dir)

    names = {path.name for path in export_dir.iterdir()}
    assert "ledger.json" not in names
    assert "ledger.csv" not in names
    assert "uncategorized.json" not in names
    published = "\n".join(path.read_text() for path in export_dir.glob("*.json"))
    assert "PRIVATE COFFEE ORDER" not in published
    assert "/Users/me/private.csv" not in published
    assert "PRIVATE COFFEE" not in published
