"""Persist ledger + derived tables to parquet and DuckDB."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from src.atomic import (
    atomic_build_file,
    atomic_copy_file,
    atomic_replace_directory,
    atomic_write_csv,
    atomic_write_parquet,
    atomic_write_text,
)
from src.cashflow import non_payment_frame
from src.paths import (
    EXPORT_DIR,
    FINANCE_DB,
    INGEST_MANIFEST,
    LEDGER_PARQUET,
    PUBLISH_PATH,
    RECONCILE_PARQUET,
    RECURRING_PARQUET,
    TRANSACTION_SOURCES_PARQUET,
    ensure_dirs,
)


def write_ledger(ledger: pd.DataFrame, path: Path = LEDGER_PARQUET) -> Path:
    ensure_dirs()
    if path.exists():
        atomic_copy_file(path, path.with_suffix(path.suffix + ".bak"))
    return atomic_write_parquet(ledger, path)


def write_recurring(recurring: pd.DataFrame, path: Path = RECURRING_PARQUET) -> Path:
    ensure_dirs()
    return atomic_write_parquet(recurring, path)


def write_reconciliation(frame: pd.DataFrame, path: Path = RECONCILE_PARQUET) -> Path:
    ensure_dirs()
    return atomic_write_parquet(frame, path)


def write_ingest_manifest(frame: pd.DataFrame, path: Path = INGEST_MANIFEST) -> Path:
    ensure_dirs()
    history = frame
    if path.exists():
        try:
            history = pd.concat([pd.read_parquet(path), frame], ignore_index=True)
        except Exception:  # noqa: BLE001 — a bad manifest must not block ledger recovery
            history = frame
    return atomic_write_parquet(history, path)


def last_statement_upload_at(path: Path = INGEST_MANIFEST) -> str | None:
    """Latest successful statement ingest time, or None when none have been processed."""
    if not path.exists():
        return None
    try:
        frame = pd.read_parquet(path, columns=["processed_at", "status"])
    except Exception:  # noqa: BLE001 — settings should still open if the manifest is unreadable
        return None
    if frame.empty or "processed_at" not in frame.columns:
        return None
    if "status" in frame.columns:
        frame = frame[frame["status"].isin(["parsed", "duplicate_document"])]
    stamps = pd.to_datetime(frame["processed_at"], utc=True, errors="coerce").dropna()
    if stamps.empty:
        return None
    return stamps.max().isoformat()


def write_transaction_sources(
    frame: pd.DataFrame, path: Path = TRANSACTION_SOURCES_PARQUET
) -> Path:
    """Append distinct source-document links without losing earlier provenance."""
    ensure_dirs()
    history = frame
    if path.exists():
        try:
            history = pd.concat([pd.read_parquet(path), frame], ignore_index=True).drop_duplicates()
        except Exception:  # noqa: BLE001 — recovery can reconstruct links from the inbox
            history = frame
    return atomic_write_parquet(history, path)


def rebuild_duckdb(
    ledger: pd.DataFrame,
    recurring: pd.DataFrame,
    reconciliation: pd.DataFrame,
    db_path: Path = FINANCE_DB,
) -> Path:
    ensure_dirs()

    def build(path: Path) -> None:
        con = duckdb.connect(str(path))
        try:
            con.register("ledger_df", ledger)
            con.register("recurring_df", recurring)
            con.register("reconciliation_df", reconciliation)
            con.register("spend_df", non_payment_frame(ledger))
            con.execute("CREATE TABLE ledger AS SELECT * FROM ledger_df")
            con.execute("CREATE TABLE recurring AS SELECT * FROM recurring_df")
            con.execute("CREATE TABLE reconciliation AS SELECT * FROM reconciliation_df")
            con.execute(
                """
                CREATE TABLE category_monthly AS
                SELECT
                  strftime(CAST(posted_date AS DATE), '%Y-%m') AS month,
                  COALESCE(category, 'Uncategorized') AS category,
                  COALESCE(subcategory, '') AS subcategory,
                  SUM(amount) AS total,
                  COUNT(*) AS txn_count
                FROM spend_df
                GROUP BY 1, 2, 3
                ORDER BY 1, 2
                """
            )
            con.execute(
                """
                CREATE TABLE merchant_monthly AS
                SELECT
                  strftime(CAST(posted_date AS DATE), '%Y-%m') AS month,
                  COALESCE(canonical_merchant, normalized_merchant) AS merchant,
                  canonical_merchant IS NOT NULL AS is_canonical,
                  SUM(amount) AS total,
                  COUNT(*) AS txn_count
                FROM spend_df
                GROUP BY 1, 2, 3
                ORDER BY 1, 4 DESC
                """
            )
        finally:
            con.close()

    return atomic_build_file(db_path, build)


def export_for_dashboard(
    ledger: pd.DataFrame,
    recurring: pd.DataFrame,
    reconciliation: pd.DataFrame,
    publish_path: Path = PUBLISH_PATH,
    export_dir: Path = EXPORT_DIR,
) -> Path:
    """Write JSON/CSV artifacts the static dashboard consumes."""
    ensure_dirs()
    mode = "aggregates_only"
    include_merchant_names = False
    if publish_path.exists():
        with publish_path.open() as f:
            config = yaml.safe_load(f) or {}
            mode = config.get("mode", mode)
            include_merchant_names = bool(config.get("include_merchant_names", False))
    if mode not in {"aggregates_only", "full"}:
        raise ValueError(f"Unsupported publish mode: {mode}")

    spend = non_payment_frame(ledger)
    category_monthly = (
        spend.assign(
            month=lambda d: pd.to_datetime(d["posted_date"]).dt.strftime("%Y-%m"),
            category=lambda d: d["category"].fillna("Uncategorized"),
            subcategory=lambda d: d["subcategory"].fillna(""),
        )
        .groupby(["month", "category", "subcategory"], as_index=False)
        .agg(total=("amount", "sum"), txn_count=("amount", "count"))
        .sort_values(["month", "category"])
    )

    merchant_totals = (
        spend.assign(
            merchant=lambda d: d["canonical_merchant"].fillna(d["normalized_merchant"]),
            canonical=lambda d: d["canonical_merchant"].notna(),
        )
        .groupby(["merchant", "canonical"], as_index=False)
        .agg(total=("amount", "sum"), txn_count=("amount", "count"))
        .sort_values("total", ascending=False)
    )

    uncategorized = ledger[
        ledger["classified_by"].isna()
        | (ledger["category"].isna())
        | (ledger["category"] == "Uncategorized")
        | (ledger["classified_by"] == "ai")
    ].copy()

    summary = {
        "mode": mode,
        "txn_count": int(len(ledger)),
        "uncategorized_count": int(len(uncategorized)),
        "canonical_count": int(ledger["canonical_merchant"].notna().sum())
        if "canonical_merchant" in ledger.columns
        else 0,
        "unknown_merchant_count": int(
            ledger.loc[ledger["canonical_merchant"].isna(), "normalized_merchant"].nunique()
        )
        if "canonical_merchant" in ledger.columns
        else 0,
        "recurring_count": int(recurring["is_recurring"].sum())
        if not recurring.empty and "is_recurring" in recurring.columns
        else 0,
    }
    recurring_export = recurring.copy()
    reconciliation_export = reconciliation.copy()
    if not include_merchant_names:
        if "normalized_merchant" in recurring_export.columns:
            recurring_export["normalized_merchant"] = "Private recurring charge"
        if "matched_merchant" in reconciliation_export.columns:
            reconciliation_export["matched_merchant"] = None
        if "bill" in reconciliation_export.columns:
            reconciliation_export["bill"] = "Expected recurring bill"
    def write_json(frame: pd.DataFrame, target: Path) -> None:
        target.write_text(frame.to_json(orient="records", date_format="iso"))

    def build(out: Path) -> None:
        # Aggregate exports are an allowlist. They intentionally contain no
        # transaction-level descriptions, identifiers, or local source paths.
        atomic_write_csv(category_monthly, out / "category_monthly.csv")
        write_json(category_monthly, out / "category_monthly.json")
        atomic_write_csv(recurring_export, out / "recurring.csv")
        write_json(recurring_export, out / "recurring.json")
        atomic_write_csv(reconciliation_export, out / "reconciliation.csv")
        write_json(reconciliation_export, out / "reconciliation.json")
        if include_merchant_names:
            atomic_write_csv(merchant_totals, out / "merchants.csv")
            write_json(merchant_totals, out / "merchants.json")
        else:
            atomic_write_text(out / "merchants.json", "[]")
        atomic_write_text(out / "summary.json", json.dumps(summary, indent=2))

        if mode == "full":
            published_ledger = ledger.drop(columns=["source_file", "source_document_id"], errors="ignore")
            atomic_write_csv(published_ledger, out / "ledger.csv")
            write_json(published_ledger, out / "ledger.json")
            atomic_write_csv(uncategorized, out / "uncategorized.csv")
            write_json(uncategorized, out / "uncategorized.json")

    return atomic_replace_directory(export_dir, build)
