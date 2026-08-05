"""Persist ledger + derived tables to parquet and DuckDB."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from src.paths import (
    EXPORT_DIR,
    FINANCE_DB,
    LEDGER_PARQUET,
    PUBLISH_PATH,
    RECONCILE_PARQUET,
    RECURRING_PARQUET,
    ensure_dirs,
)


def write_ledger(ledger: pd.DataFrame, path: Path = LEDGER_PARQUET) -> Path:
    ensure_dirs()
    ledger.to_parquet(path, index=False)
    return path


def write_recurring(recurring: pd.DataFrame, path: Path = RECURRING_PARQUET) -> Path:
    ensure_dirs()
    recurring.to_parquet(path, index=False)
    return path


def write_reconciliation(frame: pd.DataFrame, path: Path = RECONCILE_PARQUET) -> Path:
    ensure_dirs()
    frame.to_parquet(path, index=False)
    return path


def rebuild_duckdb(
    ledger: pd.DataFrame,
    recurring: pd.DataFrame,
    reconciliation: pd.DataFrame,
    db_path: Path = FINANCE_DB,
) -> Path:
    ensure_dirs()
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    con.register("ledger_df", ledger)
    con.register("recurring_df", recurring)
    con.register("reconciliation_df", reconciliation)
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
        FROM ledger
        WHERE amount > 0
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
        FROM ledger
        WHERE amount > 0
        GROUP BY 1, 2, 3
        ORDER BY 1, 4 DESC
        """
    )
    con.close()
    return db_path


def export_for_dashboard(
    ledger: pd.DataFrame,
    recurring: pd.DataFrame,
    reconciliation: pd.DataFrame,
    publish_path: Path = PUBLISH_PATH,
) -> Path:
    """Write JSON/CSV artifacts the static dashboard consumes."""
    ensure_dirs()
    mode = "aggregates_only"
    if publish_path.exists():
        with publish_path.open() as f:
            mode = (yaml.safe_load(f) or {}).get("mode", mode)

    category_monthly = (
        ledger.assign(
            month=lambda d: pd.to_datetime(d["posted_date"]).dt.strftime("%Y-%m"),
            category=lambda d: d["category"].fillna("Uncategorized"),
            subcategory=lambda d: d["subcategory"].fillna(""),
        )
        .loc[lambda d: d["amount"] > 0]
        .groupby(["month", "category", "subcategory"], as_index=False)
        .agg(total=("amount", "sum"), txn_count=("amount", "count"))
        .sort_values(["month", "category"])
    )

    merchant_totals = (
        ledger.assign(
            merchant=lambda d: d["canonical_merchant"].fillna(d["normalized_merchant"]),
            canonical=lambda d: d["canonical_merchant"].notna(),
        )
        .loc[lambda d: d["amount"] > 0]
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

    out = EXPORT_DIR
    category_monthly.to_csv(out / "category_monthly.csv", index=False)
    recurring.to_csv(out / "recurring.csv", index=False)
    reconciliation.to_csv(out / "reconciliation.csv", index=False)
    merchant_totals.to_csv(out / "merchants.csv", index=False)
    category_monthly.to_json(out / "category_monthly.json", orient="records", date_format="iso")
    recurring.to_json(out / "recurring.json", orient="records", date_format="iso")
    reconciliation.to_json(out / "reconciliation.json", orient="records", date_format="iso")
    merchant_totals.to_json(out / "merchants.json", orient="records", date_format="iso")

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
    (out / "summary.json").write_text(json.dumps(summary, indent=2))

    if mode == "full":
        ledger.to_csv(out / "ledger.csv", index=False)
        ledger.to_json(out / "ledger.json", orient="records", date_format="iso")
        uncategorized.to_csv(out / "uncategorized.csv", index=False)

    # Kept local for the watchlist page even in aggregates_only preview builds
    uncategorized.to_json(out / "uncategorized.json", orient="records", date_format="iso")

    return out
