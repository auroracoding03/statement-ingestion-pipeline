"""Pipeline stage orchestration shared by the CLI and the HTTP API.

All ledger mutations funnel through here so both entry points get the same
semantics and the same write lock.
"""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd
from filelock import FileLock

from src.ai_suggest import propose_canonicals_for_clusters, suggest
from src import ai_review
from src.atomic import atomic_write_parquet
from src.classify import classify as apply_rules
from src.extract import archive_statement, extract_statements
from src.merchants import canonicalize, cluster_unknowns
from src.migrate import migrate_ledger, needs_migration
from src.normalize import CLASSIFICATION_COLUMNS, LEDGER_COLUMNS, normalize, transaction_sources
from src.paths import (
    INBOX,
    INGEST_MANIFEST,
    LEDGER_LOCK,
    LEDGER_PARQUET,
    PROPOSALS_PARQUET,
    TRANSACTION_SOURCES_PARQUET,
    ensure_dirs,
)
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
    """Parse the active inbox and append unknown transactions to the ledger."""
    ensure_dirs()
    extraction = extract_statements(INBOX)
    failed = list(extraction.errors)

    if extraction.manifest.empty:
        prior = load_ledger() if LEDGER_PARQUET.exists() else pd.DataFrame(columns=ALL_COLUMNS)
        return {
            "ingested": 0,
            "total": len(prior),
            "failed": [],
            "archived": [],
            "message": "No statement files found.",
        }

    if not extraction.successful and failed:
        with ledger_lock():
            write_ingest_manifest(extraction.manifest, path=INGEST_MANIFEST)
        prior = load_ledger() if LEDGER_PARQUET.exists() else pd.DataFrame(columns=ALL_COLUMNS)
        return {
            "error": "Statement parsing failed; ledger was not changed.",
            "details": failed,
            "failed": failed,
            "ingested": 0,
            "total": len(prior),
            "archived": [],
        }

    raw = extraction.frame
    with ledger_lock():
        prior = load_ledger() if LEDGER_PARQUET.exists() else pd.DataFrame(columns=ALL_COLUMNS)
        prior = _ensure_columns(prior)
        prior_ids = set(prior["txn_id"].dropna()) if not prior.empty else set()
        combined = prior
        path = LEDGER_PARQUET if LEDGER_PARQUET.exists() else None
        if not raw.empty:
            incoming = normalize(raw)
            source_links = transaction_sources(raw)
            new_rows = incoming[~incoming["txn_id"].isin(prior_ids)].copy()
            if not new_rows.empty:
                new_rows = canonicalize(_ensure_columns(new_rows))
                if prior.empty:
                    combined = new_rows
                else:
                    combined = pd.concat([prior, new_rows], ignore_index=True)
                combined = combined.drop_duplicates(subset=["txn_id"], keep="first")
                combined = _ensure_columns(combined)
                combined["posted_date"] = pd.to_datetime(combined["posted_date"]).dt.date
                path = write_ledger(combined, path=LEDGER_PARQUET)
            write_transaction_sources(source_links, path=TRANSACTION_SOURCES_PARQUET)
        write_ingest_manifest(extraction.manifest, path=INGEST_MANIFEST)

    archived: list[str] = []
    for card, statement in extraction.successful:
        try:
            archived.append(str(archive_statement(statement, inbox=INBOX, card=card)))
        except OSError as exc:
            failed.append(f"{statement.name}: {type(exc).__name__}: {exc}")

    new_count = 0
    if not combined.empty:
        new_count = int((~combined["txn_id"].isin(prior_ids)).sum())

    result = {
        "ingested": new_count,
        "total": len(combined),
        "failed": failed,
        "archived": archived,
        "details": failed,
    }
    if path is not None:
        result["path"] = str(path)
    if failed:
        result["message"] = (
            f"Ingested {new_count} new transactions; processed {len(archived)} statements; "
            f"{len(failed)} need attention."
        )
    elif new_count == 0:
        result["message"] = (
            f"No new transactions ({len(combined)} already in ledger)."
            if len(combined)
            else "Nothing ingested."
        )
    return result


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


def run_ai_analysis(mode: str = "full") -> dict:
    """Create resumable local-AI proposals without mutating the ledger."""
    with ledger_lock():
        return ai_review.run_analysis(load_ledger(), mode=mode)


def apply_ai_decisions(decisions: list[dict]) -> dict:
    """Apply explicitly approved AI proposals under the ledger write lock."""
    with ledger_lock():
        ledger = load_ledger()
        if ledger.empty:
            return {"error": "No ledger yet. Run ingest first."}
        return ai_review.apply_decisions(
            ledger,
            lambda frame: write_ledger(_ensure_columns(frame)),
            decisions,
            ledger_path=LEDGER_PARQUET,
        )


def rollback_ai_application() -> dict:
    """Restore the snapshot from the most recent AI approval batch."""
    with ledger_lock():
        return ai_review.rollback_latest(LEDGER_PARQUET)


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


def open_matches_for_rule(ledger: pd.DataFrame, rule: dict) -> list[dict]:
    """Open review rows that would be classified by ``rule``. Does not write."""
    from src.classify import _compile_rules, _match_rule
    from src.review import needs_review

    compiled = _compile_rules({"rules": [rule]})
    if not compiled or ledger.empty:
        return []
    compiled_rule = compiled[0]
    matches: list[dict] = []
    for _, row in ledger.iterrows():
        if not needs_review(row):
            continue
        canonical = str(row.get("canonical_merchant") or "")
        merchant = str(row.get("normalized_merchant") or "")
        raw = str(row.get("raw_description") or "")
        if not _match_rule(compiled_rule, canonical=canonical, merchant=merchant, raw=raw):
            continue
        label = canonical or merchant or raw
        matches.append(
            {
                "txn_id": str(row["txn_id"]),
                "merchant": label,
                "amount": round(float(row.get("amount") or 0), 2),
            }
        )
    return matches


def apply_rule_to_open_review(rule: dict) -> list[str]:
    """Classify open review rows that match a newly saved rule.

    Manual / already-final rows are left alone. Returns the txn_ids updated.
    """
    from src.classify import _compile_rules, _match_rule
    from src.review import needs_review

    compiled = _compile_rules({"rules": [rule]})
    if not compiled:
        return []
    compiled_rule = compiled[0]

    with ledger_lock():
        ledger = load_ledger()
        if ledger.empty:
            return []

        applied: list[str] = []
        for idx, row in ledger.iterrows():
            if not needs_review(row):
                continue
            canonical = str(row.get("canonical_merchant") or "")
            merchant = str(row.get("normalized_merchant") or "")
            raw = str(row.get("raw_description") or "")
            if not _match_rule(compiled_rule, canonical=canonical, merchant=merchant, raw=raw):
                continue
            ledger.at[idx, "category"] = compiled_rule["category"]
            ledger.at[idx, "subcategory"] = compiled_rule["subcategory"]
            ledger.at[idx, "classified_by"] = "rule"
            ledger.at[idx, "proposed_category"] = None
            ledger.at[idx, "proposed_subcategory"] = None
            applied.append(str(row["txn_id"]))

        if applied:
            write_ledger(_ensure_columns(ledger))
        return applied


def apply_bulk_transactions(
    txn_ids: list[str],
    *,
    category: str | None = None,
    subcategory: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Apply a category and/or tags to selected ledger rows. Does not write rules."""
    from src.tags import normalize_tag_ids

    cleaned_ids = [str(txn_id) for txn_id in txn_ids if str(txn_id).strip()]
    if not cleaned_ids:
        return {"error": "Select at least one transaction"}
    if category is None and tags is None:
        return {"error": "Provide a category or tags"}

    with ledger_lock():
        ledger = load_ledger()
        if ledger.empty:
            return {"error": "Ledger is empty"}
        match = ledger["txn_id"].astype(str).isin(cleaned_ids)
        if not match.any():
            return {"error": "None of those transactions were found"}
        if category is not None:
            cleaned_category = category.strip()
            if not cleaned_category:
                return {"error": "Category is required"}
            cleaned_sub = " ".join((subcategory or "").split()).strip()
            ledger.loc[match, "category"] = cleaned_category
            ledger.loc[match, "subcategory"] = cleaned_sub
            ledger.loc[match, "classified_by"] = "manual"
            ledger.loc[match, "proposed_category"] = None
            ledger.loc[match, "proposed_subcategory"] = None
        if tags is not None:
            normalized = normalize_tag_ids(tags)
            for idx in ledger.index[match]:
                ledger.at[idx, "tags"] = list(normalized)
        write_ledger(_ensure_columns(ledger))
        updated = ledger.loc[match, "txn_id"].astype(str).tolist()
    return {"updated": updated, "count": len(updated)}



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


def rename_canonical_on_ledger(old: str, new: str) -> int:
    """Stamp a renamed canonical onto existing ledger rows."""
    return reassign_canonical_on_ledger(old, new)


def reassign_canonical_on_ledger(
    old: str,
    new: str,
    *,
    category: str | None = None,
    subcategory: str = "",
) -> int:
    """Rewrite ledger rows from one canonical name to another.

    Optionally apply a category to those same rows only, not every row already
    using the target name.
    """
    cleaned_old = " ".join((old or "").split()).strip()
    cleaned_new = " ".join((new or "").split()).strip()
    if not cleaned_old or not cleaned_new or cleaned_old.lower() == cleaned_new.lower():
        return 0
    with ledger_lock():
        ledger = load_ledger()
        if ledger.empty or "canonical_merchant" not in ledger.columns:
            return 0
        names = ledger["canonical_merchant"].fillna("").astype(str)
        match = names.str.casefold() == cleaned_old.casefold()
        count = int(match.sum())
        if not count:
            return 0
        ledger.loc[match, "canonical_merchant"] = cleaned_new
        if category:
            cleaned_sub = " ".join((subcategory or "").split()).strip()
            ledger.loc[match, "category"] = " ".join(category.split()).strip()
            ledger.loc[match, "subcategory"] = cleaned_sub
            ledger.loc[match, "classified_by"] = "manual"
            ledger.loc[match, "proposed_category"] = None
            ledger.loc[match, "proposed_subcategory"] = None
        write_ledger(_ensure_columns(ledger))
        return count


def apply_category_to_canonical(canonical: str, category: str, subcategory: str = "") -> int:
    """Apply a merchant default category to every row with this canonical name."""
    cleaned = " ".join(category.split()).strip()
    if not canonical or not cleaned:
        return 0
    cleaned_sub = " ".join((subcategory or "").split()).strip()
    with ledger_lock():
        ledger = load_ledger()
        if ledger.empty or "canonical_merchant" not in ledger.columns:
            return 0
        match = ledger["canonical_merchant"].fillna("").astype(str) == canonical
        count = int(match.sum())
        if count:
            ledger.loc[match, "category"] = cleaned
            ledger.loc[match, "subcategory"] = cleaned_sub
            ledger.loc[match, "classified_by"] = "manual"
            ledger.loc[match, "proposed_category"] = None
            ledger.loc[match, "proposed_subcategory"] = None
            write_ledger(_ensure_columns(ledger))
        return count


def rewrite_ledger_category(
    category: str,
    subcategory: str | None = None,
    *,
    action: str,
    reassign_category: str = "",
    reassign_subcategory: str = "",
) -> int:
    """Unassign or retarget ledger rows that use a category being deleted."""
    with ledger_lock():
        ledger = load_ledger()
        if ledger.empty:
            return 0
        cats = ledger["category"].fillna("").astype(str).str.strip()
        if subcategory is None:
            match = cats == category
        else:
            subs = ledger["subcategory"].fillna("").astype(str).str.strip()
            match = (cats == category) & (subs == subcategory)
        count = int(match.sum())
        if not count:
            return 0
        if action == "reassign":
            ledger.loc[match, "category"] = reassign_category
            ledger.loc[match, "subcategory"] = reassign_subcategory
            ledger.loc[match, "classified_by"] = "manual"
        else:
            ledger.loc[match, "category"] = "Uncategorized"
            ledger.loc[match, "subcategory"] = ""
            ledger.loc[match, "classified_by"] = None
        ledger.loc[match, "proposed_category"] = None
        ledger.loc[match, "proposed_subcategory"] = None
        write_ledger(_ensure_columns(ledger))
        return count
