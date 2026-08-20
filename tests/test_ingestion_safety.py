"""Regression coverage for ingestion correctness, privacy, and recovery."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

import src.pipeline as pipeline
import src.store as store
from config.parsers.generic_csv import parse_generic_csv
from src.extract import extract_statements
from src.normalize import normalize, transaction_sources
from src.recurring import detect_recurring
from src.store import export_for_dashboard
from src.upload_context import sidecar_path, write_upload_context


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


def test_failed_parse_does_not_skip_identical_copy_with_product_sidecar(tmp_path: Path, monkeypatch):
    inbox = tmp_path / "inbox"
    leftover = inbox / "bankofamerica"
    tagged = inbox / "bankofamerica-named-card"
    leftover.mkdir(parents=True)
    tagged.mkdir(parents=True)
    content = b"%PDF-1.4 statement-bytes\n"
    leftover_file = leftover / "eStmt.pdf"
    tagged_file = tagged / "eStmt.pdf"
    leftover_file.write_bytes(content)
    tagged_file.write_bytes(content)
    write_upload_context(leftover_file, issuer="Bank of America", product=None)
    write_upload_context(tagged_file, issuer="Bank of America", product="Named Card")

    def fake_parser(path: Path, card: str, metadata=None):
        metadata = metadata or {}
        if not metadata.get("card_product"):
            raise ValueError("Bank of America card product was not found")
        return pd.DataFrame(
            [
                {
                    "posted_date": "2026-01-17",
                    "amount": 4.50,
                    "raw_description": "COFFEE SHOP",
                    "card": card,
                }
            ]
        )

    monkeypatch.setattr("src.extract.resolve_parser", lambda card, suffix: fake_parser)

    result = extract_statements(inbox)

    assert result.errors
    assert "card product was not found" in result.errors[0]
    assert len(result.frame) == 1
    assert result.frame["amount"].tolist() == [4.50]
    assert set(result.manifest["status"]) == {"failed", "parsed"}


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


def test_parser_failure_does_not_block_sibling_documents(tmp_path: Path, monkeypatch):
    inbox, ledger_path = _patch_ingest_paths(tmp_path, monkeypatch)
    card_dir = inbox / "generic"
    card_dir.mkdir(parents=True)
    (card_dir / "valid.csv").write_text("Date,Description,Amount\n2026-01-01,Coffee,10.00\n")
    (card_dir / "broken.csv").write_text("Date,Description\n2026-01-02,Bad row\n")

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

    result = pipeline.run_ingest()

    assert "error" not in result
    assert result["ingested"] == 1
    assert result["failed"]
    assert (card_dir / "broken.csv").exists()
    assert not (card_dir / "valid.csv").exists()
    assert (inbox / "_ingested" / "generic" / "valid.csv").exists()
    ledger = pd.read_parquet(ledger_path)
    assert "EXISTING" in set(ledger["raw_description"])
    assert "Coffee" in set(ledger["raw_description"])


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


def _patch_ingest_paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    inbox = tmp_path / "inbox"
    data = tmp_path / "data"
    inbox.mkdir()
    data.mkdir()
    ledger = data / "ledger.parquet"
    monkeypatch.setattr(pipeline, "INBOX", inbox)
    monkeypatch.setattr(pipeline, "LEDGER_PARQUET", ledger)
    monkeypatch.setattr(pipeline, "LEDGER_LOCK", data / "ledger.lock")
    monkeypatch.setattr(pipeline, "INGEST_MANIFEST", data / "ingestion_manifest.parquet")
    monkeypatch.setattr(pipeline, "TRANSACTION_SOURCES_PARQUET", data / "transaction_sources.parquet")
    monkeypatch.setattr(pipeline, "SUPPRESSED_TXN_PATH", data / "suppressed_txn_ids.parquet")
    monkeypatch.setattr(pipeline, "ensure_dirs", lambda: None)
    return inbox, ledger


def _write_csv(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Date,Description,Amount\n" + body)


def test_ingest_appends_without_dropping_classified_rows(tmp_path: Path, monkeypatch):
    inbox, ledger_path = _patch_ingest_paths(tmp_path, monkeypatch)
    _write_csv(inbox / "generic" / "jan.csv", "2026-01-01,Coffee,10.00\n")
    first = pipeline.run_ingest()
    assert first["ingested"] == 1

    ledger = pd.read_parquet(ledger_path)
    ledger["category"] = "Food"
    ledger["classified_by"] = "manual"
    ledger["canonical_merchant"] = "Coffee Cart"
    ledger["merchant_source"] = "manual"
    ledger.to_parquet(ledger_path, index=False)

    _write_csv(inbox / "generic" / "feb.csv", "2026-01-01,Coffee,10.00\n2026-02-01,Tea,4.00\n")
    second = pipeline.run_ingest()

    assert second["ingested"] == 1
    updated = pd.read_parquet(ledger_path)
    coffee = updated[updated["raw_description"] == "Coffee"].iloc[0]
    assert coffee["category"] == "Food"
    assert coffee["classified_by"] == "manual"
    assert coffee["canonical_merchant"] == "Coffee Cart"
    assert "Tea" in set(updated["raw_description"])


def test_deleted_transaction_is_not_reingested(tmp_path: Path, monkeypatch):
    inbox, ledger_path = _patch_ingest_paths(tmp_path, monkeypatch)
    _write_csv(inbox / "generic" / "jan.csv", "2026-01-01,Coffee,10.00\n")
    first = pipeline.run_ingest()
    assert first["ingested"] == 1

    ledger = pd.read_parquet(ledger_path)
    txn_id = str(ledger.iloc[0]["txn_id"])
    removed = pipeline.delete_transaction(txn_id)
    assert removed == {"deleted": True, "txn_id": txn_id}
    assert pd.read_parquet(ledger_path).empty

    _write_csv(inbox / "generic" / "jan.csv", "2026-01-01,Coffee,10.00\n")
    second = pipeline.run_ingest()
    assert second["ingested"] == 0
    assert pd.read_parquet(ledger_path).empty


def test_ingest_normalizes_overlapping_pending_documents_as_one_batch(tmp_path: Path, monkeypatch):
    inbox, ledger_path = _patch_ingest_paths(tmp_path, monkeypatch)
    _write_csv(inbox / "generic" / "a.csv", "2026-01-06,COFFEE CART,4.25\n2026-01-06,COFFEE CART,4.25\n")
    _write_csv(inbox / "generic" / "b.csv", "2026-01-06,COFFEE CART,4.25\n")

    result = pipeline.run_ingest()

    assert result["ingested"] == 2
    ledger = pd.read_parquet(ledger_path)
    assert len(ledger) == 2
    assert ledger["txn_id"].nunique() == 2


def test_reupload_of_same_bytes_adds_zero_rows_and_archives(tmp_path: Path, monkeypatch):
    inbox, ledger_path = _patch_ingest_paths(tmp_path, monkeypatch)
    body = "2026-01-01,Coffee,10.00\n"
    _write_csv(inbox / "generic" / "jan.csv", body)
    pipeline.run_ingest()
    _write_csv(inbox / "generic" / "copy.csv", body)

    result = pipeline.run_ingest()

    assert result["ingested"] == 0
    assert len(pd.read_parquet(ledger_path)) == 1
    assert not (inbox / "generic" / "copy.csv").exists()
    assert (inbox / "_ingested" / "generic" / "copy.csv").exists()


def test_all_failed_documents_leave_ledger_untouched(tmp_path: Path, monkeypatch):
    inbox, ledger_path = _patch_ingest_paths(tmp_path, monkeypatch)
    _write_csv(inbox / "generic" / "ok.csv", "2026-01-01,Coffee,10.00\n")
    pipeline.run_ingest()
    before = ledger_path.read_bytes()
    (inbox / "generic" / "broken.csv").write_text("Date,Description\n2026-01-02,Bad row\n")

    result = pipeline.run_ingest()

    assert "error" in result
    assert ledger_path.read_bytes() == before
    assert (inbox / "generic" / "broken.csv").exists()


def test_retry_after_archive_failure_does_not_duplicate_transactions(tmp_path: Path, monkeypatch):
    inbox, ledger_path = _patch_ingest_paths(tmp_path, monkeypatch)
    _write_csv(inbox / "generic" / "jan.csv", "2026-01-01,Coffee,10.00\n")

    def fail_archive(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "archive_statement", fail_archive)
    first = pipeline.run_ingest()
    assert first["ingested"] == 1
    assert (inbox / "generic" / "jan.csv").exists()

    import src.extract as extract

    monkeypatch.setattr(pipeline, "archive_statement", extract.archive_statement)
    second = pipeline.run_ingest()

    assert second["ingested"] == 0
    assert len(pd.read_parquet(ledger_path)) == 1
    assert not (inbox / "generic" / "jan.csv").exists()


def test_successful_sidecar_moves_with_statement(tmp_path: Path, monkeypatch):
    inbox, _ledger_path = _patch_ingest_paths(tmp_path, monkeypatch)
    statement = inbox / "generic" / "jan.csv"
    _write_csv(statement, "2026-01-01,Coffee,10.00\n")
    write_upload_context(statement, issuer="Chase", product="Sapphire Preferred")

    pipeline.run_ingest()

    archived = inbox / "_ingested" / "generic" / "jan.csv"
    assert archived.exists()
    assert sidecar_path(archived).exists()
    assert not statement.exists()
    assert not sidecar_path(statement).exists()


def test_ingest_does_not_touch_rules_or_merchants(tmp_path: Path, monkeypatch):
    import src.paths as paths

    inbox, _ledger_path = _patch_ingest_paths(tmp_path, monkeypatch)
    _write_csv(inbox / "generic" / "jan.csv", "2026-01-01,Coffee,10.00\n")
    rules = paths.RULES_PATH.read_text(encoding="utf-8")
    merchants = paths.MERCHANTS_PATH.read_text(encoding="utf-8")

    pipeline.run_ingest()

    assert paths.RULES_PATH.read_text(encoding="utf-8") == rules
    assert paths.MERCHANTS_PATH.read_text(encoding="utf-8") == merchants
