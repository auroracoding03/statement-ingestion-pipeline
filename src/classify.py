"""Deterministic classification via ordered rules.yaml.

Match precedence per rule, most specific first:
  1. merchant_canonical  - exact match on the curated canonical merchant
  2. merchant_exact      - exact match on the normalized merchant
  3. merchant_regex      - regex against canonical, then normalized, then raw

A canonical merchant declared in merchants.yaml may also carry a default
category, which applies only when no rule matched.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml
from filelock import FileLock

from src.atomic import atomic_write_text
from src import paths
from src.merchants import merchant_defaults


def _path(path: Path | None) -> Path:
    """Resolve lazily so tests and runtime overrides of paths.RULES_PATH apply."""
    return path if path is not None else paths.RULES_PATH


def load_rules(path: Path | None = None) -> dict:
    target = _path(path)
    if not target.exists():
        return {}
    # Always UTF-8: review-created regexes can include en/em dashes from
    # statement text, and Windows defaults to a locale encoding that then
    # fails to reload the file (empty Rules UI via /api/rules 500).
    with target.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_rules(data: dict, path: Path | None = None) -> None:
    target = _path(path)
    atomic_write_text(target, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


_DASH_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2015": "-",  # horizontal bar
        "\u2212": "-",  # minus sign
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)


def rule_pattern_from_merchant(merchant: str) -> str:
    """Build a case-insensitive regex from a normalized merchant string.

    Fancy punctuation from PDF/CSV text is folded to ASCII so Windows-safe
    YAML reloads stay reliable even if a reader forgets ``encoding='utf-8'``.
    """
    tokens: list[str] = []
    for token in str(merchant).translate(_DASH_TRANSLATION).split():
        if token:
            tokens.append(re.escape(token))
    return "(?i)" + r"\s+".join(tokens) if tokens else "(?i)."


def _compile_rules(rules_doc: dict) -> list[dict]:
    compiled: list[dict] = []
    for rule in rules_doc.get("rules") or []:
        match = rule.get("match") or {}
        pattern = match.get("merchant_regex") or match.get("description_regex")
        exact = match.get("merchant_exact")
        canonical = match.get("merchant_canonical")
        if not pattern and not exact and not canonical:
            continue
        try:
            regex = re.compile(pattern) if pattern else None
        except re.error:
            regex = None
        compiled.append(
            {
                "regex": regex,
                "exact": (exact or "").upper() if exact else None,
                "canonical": canonical or None,
                "category": rule.get("category", "Uncategorized"),
                "subcategory": rule.get("subcategory") or "",
            }
        )
    return compiled


def _match_rule(rule: dict, *, canonical: str, merchant: str, raw: str) -> bool:
    if rule["canonical"]:
        return bool(canonical) and canonical.lower() == rule["canonical"].lower()
    if rule["exact"]:
        return merchant.upper() == rule["exact"]
    if rule["regex"]:
        haystacks = [h for h in (canonical, merchant, raw) if h]
        return any(rule["regex"].search(h) for h in haystacks)
    return False


def classify(frame: pd.DataFrame, rules_path: Path | None = None) -> pd.DataFrame:
    out = frame.copy()
    for column in ("category", "subcategory", "tags", "classified_by", "proposed_category", "proposed_subcategory"):
        if column not in out.columns:
            out[column] = None if column != "tags" else [[] for _ in range(len(out))] if len(out) else []

    if out.empty:
        if "tags" not in out.columns:
            out["tags"] = []
        return out

    from src.tags import normalize_tag_ids

    out["tags"] = out["tags"].apply(normalize_tag_ids)

    rules = _compile_rules(load_rules(rules_path))
    defaults = merchant_defaults()

    categories: list[str | None] = []
    subcategories: list[str | None] = []
    classified_by: list[str | None] = []

    for _, row in out.iterrows():
        canonical = str(row.get("canonical_merchant") or "")
        merchant = str(row.get("normalized_merchant") or "")
        raw = str(row.get("raw_description") or "")

        hit_cat = None
        hit_sub = None
        source = None

        for rule in rules:
            if _match_rule(rule, canonical=canonical, merchant=merchant, raw=raw):
                hit_cat = rule["category"]
                hit_sub = rule["subcategory"]
                source = "rule"
                break

        if hit_cat is None and canonical and canonical in defaults:
            hit_cat = defaults[canonical]["category"]
            hit_sub = defaults[canonical]["subcategory"]
            source = "merchant"

        categories.append(hit_cat)
        subcategories.append(hit_sub)
        classified_by.append(source)

    out["category"] = categories
    out["subcategory"] = subcategories
    out["classified_by"] = classified_by
    return out


def append_rule(
    *,
    merchant_regex: str | None = None,
    merchant_canonical: str | None = None,
    category: str,
    subcategory: str = "",
    rules_path: Path | None = None,
) -> dict:
    """Prepend a rule. Prefer a canonical match when the merchant is known."""
    if not merchant_regex and not merchant_canonical:
        raise ValueError("append_rule requires merchant_regex or merchant_canonical")

    target = _path(rules_path)
    with FileLock(f"{target}.lock"):
        doc = load_rules(target)
        rules = doc.setdefault("rules", [])
        match = (
            {"merchant_canonical": merchant_canonical}
            if merchant_canonical
            else {"merchant_regex": merchant_regex}
        )
        cleaned_sub = " ".join((subcategory or "").split()).strip()
        rule = {"match": match, "category": category, "subcategory": cleaned_sub}
        rules.insert(0, rule)

        cats = doc.setdefault("categories", [])
        if category not in cats:
            cats.append(category)
        _ensure_subcategory(doc, category, cleaned_sub)
        save_rules(doc, target)
        return rule


def append_category(category: str, rules_path: Path | None = None) -> list[str]:
    """Add a primary spend category to the vocabulary list."""
    cleaned = " ".join(category.split()).strip()
    if not cleaned:
        raise ValueError("Category name is required")
    target = _path(rules_path)
    with FileLock(f"{target}.lock"):
        doc = load_rules(target)
        cats = doc.setdefault("categories", [])
        if cleaned not in cats:
            cats.append(cleaned)
        subs = doc.setdefault("subcategories", {})
        if not isinstance(subs, dict):
            doc["subcategories"] = {}
            subs = doc["subcategories"]
        subs.setdefault(cleaned, [])
        save_rules(doc, target)
        return list(cats)


def list_subcategories(rules_path: Path | None = None) -> dict[str, list[str]]:
    """Return ``{primary: [subcategory, ...]}`` from the managed vocabulary."""
    doc = load_rules(rules_path)
    raw = doc.get("subcategories") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for category, values in raw.items():
        key = str(category).strip()
        if not key:
            continue
        items: list[str] = []
        if isinstance(values, list):
            for value in values:
                text = " ".join(str(value).split()).strip()
                if text and text not in items:
                    items.append(text)
        out[key] = items
    # Ensure every declared primary appears, even with an empty list.
    for category in doc.get("categories") or []:
        out.setdefault(str(category), [])
    return out


def _ensure_subcategory(doc: dict, category: str, subcategory: str) -> None:
    if not category or not subcategory:
        return
    subs = doc.setdefault("subcategories", {})
    if not isinstance(subs, dict):
        doc["subcategories"] = {}
        subs = doc["subcategories"]
    bucket = subs.setdefault(category, [])
    if not isinstance(bucket, list):
        bucket = []
        subs[category] = bucket
    if subcategory not in bucket:
        bucket.append(subcategory)


def append_subcategory(
    category: str,
    subcategory: str,
    rules_path: Path | None = None,
) -> dict[str, list[str]]:
    """Register a subcategory under a primary category."""
    cleaned_category = " ".join(category.split()).strip()
    cleaned_sub = " ".join(subcategory.split()).strip()
    if not cleaned_category:
        raise ValueError("Category name is required")
    if not cleaned_sub:
        raise ValueError("Subcategory name is required")
    target = _path(rules_path)
    with FileLock(f"{target}.lock"):
        doc = load_rules(target)
        cats = doc.setdefault("categories", [])
        if cleaned_category not in cats:
            cats.append(cleaned_category)
        _ensure_subcategory(doc, cleaned_category, cleaned_sub)
        save_rules(doc, target)
        return list_subcategories(target)


def update_rule(
    index: int,
    *,
    category: str,
    subcategory: str = "",
    rules_path: Path | None = None,
) -> dict | None:
    """Update category/subcategory for the rule at ``index``. Match fields are unchanged."""
    cleaned_category = " ".join(category.split()).strip()
    if not cleaned_category:
        raise ValueError("Category name is required")
    cleaned_sub = " ".join((subcategory or "").split()).strip()

    target = _path(rules_path)
    with FileLock(f"{target}.lock"):
        doc = load_rules(target)
        rules = doc.get("rules") or []
        if index < 0 or index >= len(rules):
            return None
        rule = dict(rules[index])
        rule["category"] = cleaned_category
        rule["subcategory"] = cleaned_sub
        rules[index] = rule
        doc["rules"] = rules

        cats = doc.setdefault("categories", [])
        if cleaned_category not in cats:
            cats.append(cleaned_category)
        _ensure_subcategory(doc, cleaned_category, cleaned_sub)
        save_rules(doc, target)
        return rule


def delete_rule(index: int, rules_path: Path | None = None) -> bool:
    target = _path(rules_path)
    with FileLock(f"{target}.lock"):
        doc = load_rules(target)
        rules = doc.get("rules") or []
        if index < 0 or index >= len(rules):
            return False
        rules.pop(index)
        doc["rules"] = rules
        save_rules(doc, target)
        return True


def rewrite_merchant_canonical(old: str, new: str, rules_path: Path | None = None) -> int:
    """Rename merchant_canonical matches after a curated merchant is renamed."""
    cleaned_old = " ".join(old.split()).strip()
    cleaned_new = " ".join(new.split()).strip()
    if not cleaned_old or not cleaned_new or cleaned_old == cleaned_new:
        return 0
    target = _path(rules_path)
    with FileLock(f"{target}.lock"):
        doc = load_rules(target)
        rules = doc.get("rules") or []
        changed = 0
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            match = rule.get("match") or {}
            if str(match.get("merchant_canonical") or "") == cleaned_old:
                match["merchant_canonical"] = cleaned_new
                rule["match"] = match
                changed += 1
        if changed:
            doc["rules"] = rules
            save_rules(doc, target)
        return changed


def _rule_matches_vocab(rule: dict, category: str, subcategory: str | None) -> bool:
    if not isinstance(rule, dict):
        return False
    if str(rule.get("category") or "").strip() != category:
        return False
    if subcategory is None:
        return True
    return str(rule.get("subcategory") or "").strip() == subcategory


def rewrite_rule_vocab(
    category: str,
    subcategory: str | None = None,
    *,
    action: str,
    reassign_category: str = "",
    reassign_subcategory: str = "",
    rules_path: Path | None = None,
) -> int:
    """Remove or retarget rules that use a category/subcategory being deleted."""
    target = _path(rules_path)
    with FileLock(f"{target}.lock"):
        doc = load_rules(target)
        rules = doc.get("rules") or []
        kept: list[dict] = []
        changed = 0
        for rule in rules:
            if not _rule_matches_vocab(rule, category, subcategory):
                kept.append(rule)
                continue
            changed += 1
            if action == "reassign":
                updated = dict(rule)
                updated["category"] = reassign_category
                updated["subcategory"] = reassign_subcategory
                kept.append(updated)
        if changed:
            doc["rules"] = kept
            save_rules(doc, target)
        return changed


def drop_vocab(
    category: str,
    subcategory: str | None = None,
    rules_path: Path | None = None,
) -> bool:
    """Remove a primary or subcategory from the managed vocabulary."""
    target = _path(rules_path)
    with FileLock(f"{target}.lock"):
        doc = load_rules(target)
        cats = doc.setdefault("categories", [])
        subs = doc.setdefault("subcategories", {})
        if not isinstance(subs, dict):
            subs = {}
            doc["subcategories"] = subs
        if subcategory is None:
            if category not in cats and category not in subs:
                return False
            doc["categories"] = [name for name in cats if name != category]
            subs.pop(category, None)
        else:
            bucket = subs.get(category)
            if not isinstance(bucket, list) or subcategory not in bucket:
                return False
            subs[category] = [name for name in bucket if name != subcategory]
        save_rules(doc, target)
        return True
