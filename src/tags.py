"""Context-tag vocabulary for occasions, trips, and other labels.

Primary spend categories live in ``rules.yaml``. Tags are orthogonal: a
transaction keeps one category for money rollups and zero or more tag ids for
filtering (e.g. ``date``, ``london-paris``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml
from filelock import FileLock

from src.atomic import atomic_write_text
from src.cashflow import non_payment_frame, summarize_spend
from src import paths

TAG_KINDS = ("occasion", "trip", "other")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _path(path: Path | None) -> Path:
    return path if path is not None else paths.TAGS_PATH


def slugify(label: str) -> str:
    slug = _SLUG_RE.sub("-", label.strip().lower()).strip("-")
    return slug or "tag"


def load_tags(path: Path | None = None) -> dict:
    target = _path(path)
    if not target.exists():
        return {"tags": []}
    with target.open(encoding="utf-8") as handle:
        doc = yaml.safe_load(handle) or {}
    if not isinstance(doc, dict):
        return {"tags": []}
    tags = doc.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    return {"tags": tags}


def save_tags(data: dict, path: Path | None = None) -> None:
    target = _path(path)
    atomic_write_text(target, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def list_tags(path: Path | None = None) -> list[dict]:
    items: list[dict] = []
    for entry in load_tags(path).get("tags") or []:
        if not isinstance(entry, dict):
            continue
        tag_id = str(entry.get("id") or "").strip()
        if not tag_id:
            continue
        kind = str(entry.get("kind") or "other").strip().lower()
        if kind not in TAG_KINDS:
            kind = "other"
        items.append(
            {
                "id": tag_id,
                "label": str(entry.get("label") or tag_id).strip() or tag_id,
                "kind": kind,
            }
        )
    return items


def create_tag(
    *,
    label: str,
    kind: str = "other",
    tag_id: str | None = None,
    path: Path | None = None,
) -> dict:
    cleaned_label = " ".join(label.split()).strip()
    if not cleaned_label:
        raise ValueError("Tag label is required")
    cleaned_kind = (kind or "other").strip().lower()
    if cleaned_kind not in TAG_KINDS:
        raise ValueError(f"Unsupported tag kind: {kind!r}")
    new_id = slugify(tag_id or cleaned_label)

    target = _path(path)
    with FileLock(f"{target}.lock"):
        doc = load_tags(target)
        tags = doc.setdefault("tags", [])
        if any(isinstance(entry, dict) and entry.get("id") == new_id for entry in tags):
            raise ValueError(f"Tag already exists: {new_id}")
        entry = {"id": new_id, "label": cleaned_label, "kind": cleaned_kind}
        tags.append(entry)
        save_tags(doc, target)
        return entry


def delete_tag(tag_id: str, path: Path | None = None) -> bool:
    target = _path(path)
    with FileLock(f"{target}.lock"):
        doc = load_tags(target)
        tags = doc.get("tags") or []
        kept = [entry for entry in tags if not (isinstance(entry, dict) and entry.get("id") == tag_id)]
        if len(kept) == len(tags):
            return False
        doc["tags"] = kept
        save_tags(doc, target)
        return True


def normalize_tag_ids(values) -> list[str]:
    """Coerce a cell value into a de-duplicated list of tag ids."""
    if values is None:
        return []
    if isinstance(values, float):
        try:
            if values != values:  # NaN
                return []
        except Exception:  # noqa: BLE001
            pass
    if isinstance(values, str):
        parts = [part.strip() for part in values.replace("|", ",").split(",")]
        return [part for part in parts if part]
    if hasattr(values, "tolist") and not isinstance(values, (str, bytes, list, tuple)):
        try:
            values = values.tolist()
        except (TypeError, ValueError):
            return []
    if isinstance(values, (list, tuple, set)):
        seen: set[str] = set()
        out: list[str] = []
        for item in values:
            if item is None:
                continue
            text = str(item).strip()
            if not text or text.lower() == "nan" or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out
    text = str(values).strip()
    return [text] if text and text.lower() != "nan" else []


def spend_by_tag(ledger: pd.DataFrame, *, kind: str | None = None) -> list[dict]:
    """Net spend and category breakdown for each catalog tag, live from the ledger."""
    vocab = list_tags()
    if kind is not None and str(kind).strip():
        cleaned = str(kind).strip().lower()
        if cleaned not in TAG_KINDS:
            raise ValueError(f"Unsupported tag kind: {kind!r}")
        vocab = [item for item in vocab if item["kind"] == cleaned]

    spend = (
        non_payment_frame(ledger)
        if ledger is not None and not ledger.empty
        else pd.DataFrame()
    )
    items: list[dict] = []
    for entry in vocab:
        tag_id = entry["id"]
        if spend.empty or "tags" not in spend.columns:
            matched = spend.iloc[0:0]
        else:
            mask = spend["tags"].map(lambda value, wanted=tag_id: wanted in normalize_tag_ids(value))
            matched = spend.loc[mask]
        stats = summarize_spend(matched)
        breakdown: list[dict] = []
        if not matched.empty:
            frame = matched.assign(
                category=lambda d: d["category"].fillna("Uncategorized") if "category" in d.columns else "Uncategorized",
                subcategory=lambda d: (
                    d["subcategory"].fillna("").astype(str).str.strip()
                    if "subcategory" in d.columns
                    else ""
                ),
            )
            grouped = (
                frame.groupby(["category", "subcategory"], as_index=False)
                .agg(total=("amount", "sum"), txn_count=("amount", "count"))
                .sort_values("total", ascending=False)
            )
            breakdown = [
                {
                    "category": str(row.category),
                    "subcategory": str(row.subcategory),
                    "total": round(float(row.total), 2),
                    "txn_count": int(row.txn_count),
                }
                for row in grouped.itertuples(index=False)
            ]
        items.append(
            {
                **entry,
                "total": stats["net_spend"],
                "txn_count": stats["charge_count"],
                "breakdown": breakdown,
            }
        )
    items.sort(key=lambda item: (-float(item["total"]), str(item["label"]).lower()))
    return items
