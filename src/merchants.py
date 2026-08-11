"""Canonical merchant identity: curated aliases plus fuzzy clustering of unknowns.

`config/merchants.yaml` is the durable asset here, the same way `rules.yaml` owns
categories. AI and fuzzy clustering only ever *propose* entries; a human confirms
them through the UI or CLI before they are written back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml
from filelock import FileLock
from rapidfuzz import fuzz

from src.atomic import atomic_write_text
from src import paths
from src.normalize import merchant_identity_key

MERCHANT_SOURCES = ("alias", "ai", "manual", "none")


def _path(path: Path | None) -> Path:
    """Resolve lazily so tests and runtime overrides of paths.MERCHANTS_PATH apply."""
    return path if path is not None else paths.MERCHANTS_PATH


def load_merchants(path: Path | None = None) -> dict:
    target = _path(path)
    if not target.exists():
        return {"merchants": []}
    with target.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {"merchants": []}


def save_merchants(doc: dict, path: Path | None = None) -> None:
    target = _path(path)
    atomic_write_text(target, yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))


def _compile_merchants(doc: dict) -> list[dict]:
    compiled: list[dict] = []
    for entry in doc.get("merchants") or []:
        canonical = entry.get("canonical")
        if not canonical:
            continue
        patterns: list[re.Pattern] = []
        exacts: list[str] = []
        for alias in entry.get("aliases") or []:
            if not isinstance(alias, dict):
                # Bare string alias is treated as a literal
                exacts.append(str(alias).upper())
                continue
            if alias.get("regex"):
                try:
                    patterns.append(re.compile(alias["regex"]))
                except re.error:
                    continue
            if alias.get("exact"):
                exacts.append(str(alias["exact"]).upper())
        compiled.append(
            {
                "canonical": canonical,
                "patterns": patterns,
                "exacts": exacts,
                "category": entry.get("category"),
                "subcategory": entry.get("subcategory"),
            }
        )
    return compiled


def match_canonical(
    normalized_merchant: str,
    raw_description: str = "",
    compiled: list[dict] | None = None,
    path: Path | None = None,
) -> dict | None:
    """Return the merchant entry matching this transaction, or None."""
    entries = compiled if compiled is not None else _compile_merchants(load_merchants(path))
    merchant = str(normalized_merchant or "")
    raw = str(raw_description or "")
    upper = merchant.upper()

    for entry in entries:
        if upper in entry["exacts"]:
            return entry
        for pattern in entry["patterns"]:
            if pattern.search(merchant) or pattern.search(raw):
                return entry
    return None


def canonicalize(frame: pd.DataFrame, path: Path | None = None) -> pd.DataFrame:
    """Set canonical_merchant / merchant_source from the curated alias file.

    Manually-assigned canonical names are preserved; everything else is re-derived
    so that edits to merchants.yaml take effect on the next run.
    """
    out = frame.copy()
    if out.empty:
        for column in ("canonical_merchant", "merchant_source", "proposed_canonical"):
            if column not in out.columns:
                out[column] = None
        return out

    for column in ("canonical_merchant", "merchant_source", "proposed_canonical"):
        if column not in out.columns:
            out[column] = None

    compiled = _compile_merchants(load_merchants(path))

    canonicals: list[str | None] = []
    sources: list[str] = []
    for _, row in out.iterrows():
        if row.get("merchant_source") == "manual" and row.get("canonical_merchant"):
            canonicals.append(row["canonical_merchant"])
            sources.append("manual")
            continue
        entry = match_canonical(
            row.get("normalized_merchant") or "",
            row.get("raw_description") or "",
            compiled=compiled,
        )
        if entry:
            canonicals.append(entry["canonical"])
            sources.append("alias")
        else:
            canonicals.append(None)
            sources.append("none")

    # Object dtype keeps unmatched entries as None rather than coercing to NaN
    out["canonical_merchant"] = pd.Series(canonicals, index=out.index, dtype="object")
    out["merchant_source"] = pd.Series(sources, index=out.index, dtype="object")
    return out


def merchant_defaults(path: Path | None = None) -> dict[str, dict]:
    """canonical -> {category, subcategory} defaults declared in merchants.yaml."""
    defaults: dict[str, dict] = {}
    for entry in load_merchants(path).get("merchants") or []:
        canonical = entry.get("canonical")
        if canonical and entry.get("category"):
            defaults[canonical] = {
                "category": entry.get("category"),
                "subcategory": entry.get("subcategory") or "",
            }
    return defaults


def cluster_unknowns(
    frame: pd.DataFrame,
    threshold: int = 88,
    min_size: int = 1,
) -> list[dict]:
    """Group merchants with no canonical match into fuzzy-similar clusters.

    Returns clusters ordered by total spend so the highest-value ambiguity
    surfaces first in the review UI.
    """
    if frame.empty:
        return []

    unknown = frame[
        frame["canonical_merchant"].isna() | (frame["canonical_merchant"] == "")
    ].copy()
    if unknown.empty:
        return []

    stats = (
        unknown.groupby("normalized_merchant")
        .agg(
            txn_count=("amount", "count"),
            total_amount=("amount", "sum"),
            sample_raw=("raw_description", "first"),
        )
        .reset_index()
        .sort_values("total_amount", ascending=False)
    )

    names = list(stats["normalized_merchant"])
    assigned: set[str] = set()
    clusters: list[dict] = []

    def _same_cluster(left: str, right: str) -> bool:
        left_key = merchant_identity_key(left)
        right_key = merchant_identity_key(right)
        left_tokens = left_key.split()
        right_tokens = right_key.split()
        if not left_tokens or not right_tokens:
            return False
        # Shared city/state must not glue unrelated brands (CAVA vs EAST COBB).
        if left_tokens[0].casefold() != right_tokens[0].casefold():
            return False
        return fuzz.token_set_ratio(left_key, right_key) >= threshold

    for name in names:
        if name in assigned:
            continue
        members = [name]
        assigned.add(name)
        for other in names:
            if other in assigned:
                continue
            if _same_cluster(name, other):
                members.append(other)
                assigned.add(other)

        member_rows = stats[stats["normalized_merchant"].isin(members)]
        if int(member_rows["txn_count"].sum()) < min_size:
            continue
        clusters.append(
            {
                "cluster_id": _slug(name),
                "members": members,
                "representative": name,
                "sample_raw": str(member_rows.iloc[0]["sample_raw"]),
                "txn_count": int(member_rows["txn_count"].sum()),
                "total_amount": round(float(member_rows["total_amount"].sum()), 2),
                "proposed_canonical": None,
            }
        )

    return sorted(clusters, key=lambda c: c["total_amount"], reverse=True)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "unknown"


def alias_regex_for(members: list[str]) -> str:
    """Build a case-insensitive regex covering every variant in a cluster."""
    parts = [re.escape(m.strip()) for m in members if str(m).strip()]
    if not parts:
        return "(?i)(?!)"
    # Collapse whitespace runs so spacing differences still match
    parts = [re.sub(r"\\\s+|\s+", r"\\s+", p) for p in parts]
    return "(?i)" + "|".join(parts)


def append_merchant(
    *,
    canonical: str,
    aliases: list[dict] | None = None,
    members: list[str] | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    path: Path | None = None,
) -> dict:
    """Create or extend a canonical merchant entry.

    Pass either explicit `aliases` ({regex|exact}) or raw `members` strings from a
    fuzzy cluster, which get compiled into a single alternation regex.
    """
    target = _path(path)
    with FileLock(f"{target}.lock"):
        doc = load_merchants(target)
        entries = doc.setdefault("merchants", [])

        new_aliases: list[dict] = list(aliases or [])
        if members:
            new_aliases.append({"regex": alias_regex_for(members)})
        if not new_aliases:
            raise ValueError("append_merchant requires aliases or members")

        for entry in entries:
            if str(entry.get("canonical", "")).lower() == canonical.lower():
                existing = entry.setdefault("aliases", [])
                for alias in new_aliases:
                    if alias not in existing:
                        existing.append(alias)
                if category:
                    entry["category"] = category
                if subcategory:
                    entry["subcategory"] = subcategory
                save_merchants(doc, target)
                return entry

        entry = {"canonical": canonical}
        if category:
            entry["category"] = category
        if subcategory:
            entry["subcategory"] = subcategory
        entry["aliases"] = new_aliases
        entries.insert(0, entry)
        save_merchants(doc, target)
        return entry


def delete_merchant(canonical: str, path: Path | None = None) -> bool:
    target = _path(path)
    with FileLock(f"{target}.lock"):
        doc = load_merchants(target)
        entries = doc.get("merchants") or []
        remaining = [e for e in entries if str(e.get("canonical", "")).lower() != canonical.lower()]
        if len(remaining) == len(entries):
            return False
        doc["merchants"] = remaining
        save_merchants(doc, target)
        return True
