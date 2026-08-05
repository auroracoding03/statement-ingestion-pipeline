"""Shared path helpers rooted at the project directory."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
DATA = ROOT / "data"
CONFIG = ROOT / "config"
DASHBOARD = ROOT / "dashboard"

UI = ROOT / "ui"

RULES_PATH = CONFIG / "rules.yaml"
MERCHANTS_PATH = CONFIG / "merchants.yaml"
EXPECTED_RECURRING_PATH = CONFIG / "expected_recurring.yaml"
PUBLISH_PATH = CONFIG / "publish.yaml"
OLLAMA_PATH = CONFIG / "ollama.yaml"

LEDGER_PARQUET = DATA / "ledger.parquet"
LEDGER_LOCK = DATA / "ledger.lock"
FINANCE_DB = DATA / "finance.duckdb"
PROPOSALS_PARQUET = DATA / "proposals.parquet"
INGEST_MANIFEST = DATA / "ingestion_manifest.parquet"
TRANSACTION_SOURCES_PARQUET = DATA / "transaction_sources.parquet"
RECURRING_PARQUET = DATA / "recurring.parquet"
RECONCILE_PARQUET = DATA / "reconciliation.parquet"
EXPORT_DIR = DATA / "export"


def ensure_dirs() -> None:
    INBOX.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
