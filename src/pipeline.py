"""Pipeline stage orchestration shared by the CLI and the HTTP API.

All ledger mutations funnel through here so both entry points get the same
semantics and the same write lock.
"""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd
from filelock import FileLock

from src.ai_suggest import propose_canonicals_for_clusters, suggest
from src.atomic import atomic_write_parquet
from src.classify import classify as apply_rules
from src.extract import ExtractionError, extract_statements
from src.merchants import canonicalize, cluster_unknowns
from src.migrate import migrate_ledger, needs_migration
from src.normalize import CLASSIFICATION_COLUMNS, LEDGER_COLUMNS, normalize, transaction_sources
from src.paths import INBOX, LEDGER_LOCK, LEDGER_PARQUET, PROPOSALS_PARQUET, ensure_dirs
from src.recurring import detect_recurring, reconcile
from src.store import (
    export_for_dashboard,
    rebuild_duckdb,
    write_ledger,
    write_ingest_manifest,
    write_reconciliation,
    write_recurring,
    write_transaction_sources,
)

ALL_COLUMNS = LEDGER_COLUMNS + CLASSIFICATION_COLUMNS


@contextmanager
def ledger_lock(timeout: float = 60.0):
    """Guard read-modify-write cycles against concurrent CLI and UI runs."""
    ensure_dirs()
    lock = FileLock(str(LEDGER_LOCK), timeout=timeout)
    with lock:
        yield


def ledger_exists() -> bool:
    return LEDGER_PARQUET.exists()


def load_ledger() -> pd.DataFrame:
    if not LEDGER_PARQUET.exists():
        return pd.DataFrame(columns=ALL_COLUMNS)
    ledger = pd.read_parquet(LEDGER_PARQUET)
    if needs_migration(ledger) or not set(ALL_COLUMNS).issubset(ledger.columns):
        ledger = migrate_ledger(ledger)
    return ledger


def _ensure_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in ALL_COLUMNS:
        if column not in out.columns:
            out[column] = None
    if "tags" in out.columns:
        from src.tags import normalize_tag_ids

        out["tags"] = out["tags"].apply(normalize_tag_ids)
    return out[ALL_COLUMNS]


def run_ingest() -> dict:
    """Extract, validate, normalize, and atomically refresh the ledger."""
    ensure_dirs()
    try:
        extraction = extract_statements(INBOX)
    except ExtractionError as exc:
        # The prior ledger deliberately survives a failed batch. Keep the
        # manifest so the UI/CLI can explain exactly which document failed.
        with ledger_lock():
            write_ingest_manifest(exc.manifest)
        return {"error": "Statement parsing failed; ledger was not changed.", "details": exc.errors}

    raw = extraction.frame
    if raw.empty:
        return {"ingested": 0, "total": 0, "message": "No statement files found."}

    frame = normalize(raw)
    source_links = transaction_sources(raw)

    with ledger_lock():
        prior = pd.DataFrame(columns=ALL_COLUMNS)
        if LEDGER_PARQUET.exists():
            prior = load_ledger()
            carry = ["txn_id", *CLASSIFICATION_COLUMNS, "canonical_merchant", "merchant_source"]
            existing = [c for c in carry if c in prior.columns]
            frame = frame.drop(
                columns=[c for c in ("canonical_merchant", "merchant_source") if c in frame.columns]
            ).merge(prior[existing], on="txn_id", how="left")

        frame = _ensure_columns(frame)
        frame = canonicalize(frame)
        path = write_ledger(frame)
        write_ingest_manifest(extraction.manifest)
        write_transaction_sources(source_links)

    prior_ids = set(prior.get("txn_id", pd.Series(dtype=str)).dropna())
    new_count = int((~frame["txn_id"].isin(prior_ids)).sum())
    return {"ingested": new_count, "total": len(frame), "path": str(path)}


def run_classify(with_ai: bool = False) -> dict:
    """Re-canonicalize, apply rules, then optionally ask the AI for the tail."""
    with ledger_lock():
        ledger = load_ledger()
        if ledger.empty:
            return {"error": "No ledger yet. Run ingest first."}

        ledger = canonicalize(ledger)

        locked = ledger["classified_by"] == "manual"
        locked_rows = ledger[locked].copy()
        unlocked = ledger[~locked].copy()

        classified = apply_rules(unlocked)
        if with_ai:
            classified = suggest(classified)
            proposals = classified[classified["classified_by"] == "ai"]
            if not proposals.empty:
                atomic_write_parquet(proposals, PROPOSALS_PARQUET)

        combined = pd.concat([locked_rows, classified], ignore_index=True)
        combined = combined.drop_duplicates(subset=["txn_id"], keep="first")
        combined = _ensure_columns(combined)
        write_ledger(combined)

    return classification_counts(combined)


def classification_counts(ledger: pd.DataFrame) -> dict:
    by = ledger.get("classified_by")
    if by is None:
        return {"total": len(ledger)}
    return {
        "rule": int((by == "rule").sum()),
        "merchant": int((by == "merchant").sum()),
        "ai": int((by == "ai").sum()),
        "manual": int((by == "manual").sum()),
        "open": int(by.isna().sum() + (by == "").sum()),
        "total": int(len(ledger)),
    }


def run_build() -> dict:
    """Detect recurring bills, reconcile, rebuild DuckDB, and refresh exports."""
    with ledger_lock():
        ledger = load_ledger()
        if ledger.empty:
            return {"error": "No ledger yet. Run ingest first."}

        recurring = detect_recurring(ledger)
        reconciliation = reconcile(ledger)
        write_recurring(recurring)
        write_reconciliation(reconciliation)
        db = rebuild_duckdb(ledger, recurring, reconciliation)
        export_dir = export_for_dashboard(ledger, recurring, reconciliation)

    return {
        "duckdb": str(db),
        "export_dir": str(export_dir),
        "recurring_count": int(recurring["is_recurring"].sum()) if not recurring.empty else 0,
        "reconciliation": reconciliation.to_dict(orient="records") if not reconciliation.empty else [],
    }


def unknown_merchant_clusters(threshold: int = 88, with_ai: bool = False) -> list[dict]:
    ledger = load_ledger()
    if ledger.empty:
        return []
    clusters = cluster_unknowns(canonicalize(ledger), threshold=threshold)
    if with_ai:
        clusters = propose_canonicals_for_clusters(clusters)
    return clusters


def apply_review_decision(
    txn_id: str,
    *,
    category: str,
    subcategory: str = "",
    tags: list[str] | None = None,
) -> dict:
    """Record a human classification decision on one transaction."""
    from src.tags import normalize_tag_ids

    with ledger_lock():
        ledger = load_ledger()
        match = ledger["txn_id"] == txn_id
        if not match.any():
            return {"error": f"Unknown txn_id {txn_id}"}
        ledger.loc[match, "category"] = category
        ledger.loc[match, "subcategory"] = subcategory
        if tags is not None:
            normalized = normalize_tag_ids(tags)
            # Assign list into object-dtype cells row-wise.
            for idx in ledger.index[match]:
                ledger.at[idx, "tags"] = list(normalized)
        ledger.loc[match, "classified_by"] = "manual"
        ledger.loc[match, "proposed_category"] = None
        ledger.loc[match, "proposed_subcategory"] = None
        write_ledger(_ensure_columns(ledger))
    result = {"txn_id": txn_id, "category": category, "subcategory": subcategory}
    if tags is not None:
        result["tags"] = normalize_tag_ids(tags)
    return result



def set_canonical_for_merchants(members: list[str], canonical: str) -> int:
    """Stamp a canonical name onto every ledger row matching these variants."""
    with ledger_lock():
        ledger = load_ledger()
        if ledger.empty:
            return 0
        match = ledger["normalized_merchant"].isin(members)
        count = int(match.sum())
        ledger.loc[match, "canonical_merchant"] = canonical
        ledger.loc[match, "merchant_source"] = "manual"
        ledger.loc[match, "proposed_canonical"] = None
        write_ledger(_ensure_columns(ledger))
    return count


def recanonicalize() -> dict:
    """Re-derive canonical names for every non-manual row after a merchants edit."""
    with ledger_lock():
        ledger = load_ledger()
        if ledger.empty:
            return {"updated": 0}
        updated = canonicalize(ledger)
        write_ledger(_ensure_columns(updated))
        resolved = int(updated["canonical_merchant"].notna().sum())
    return {"updated": len(updated), "canonical": resolved}
