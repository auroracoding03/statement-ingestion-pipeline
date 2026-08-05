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

from src import paths
from src.merchants import merchant_defaults


def _path(path: Path | None) -> Path:
    """Resolve lazily so tests and runtime overrides of paths.RULES_PATH apply."""
    return path if path is not None else paths.RULES_PATH


def load_rules(path: Path | None = None) -> dict:
    target = _path(path)
    if not target.exists():
        return {}
    with target.open() as f:
        return yaml.safe_load(f) or {}


def save_rules(data: dict, path: Path | None = None) -> None:
    with _path(path).open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


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
    for column in ("category", "subcategory", "classified_by", "proposed_category", "proposed_subcategory"):
        if column not in out.columns:
            out[column] = None

    if out.empty:
        return out

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

    doc = load_rules(rules_path)
    rules = doc.setdefault("rules", [])
    match = (
        {"merchant_canonical": merchant_canonical}
        if merchant_canonical
        else {"merchant_regex": merchant_regex}
    )
    rule = {"match": match, "category": category, "subcategory": subcategory}
    rules.insert(0, rule)

    cats = doc.setdefault("categories", [])
    if category not in cats:
        cats.append(category)
    save_rules(doc, rules_path)
    return rule


def delete_rule(index: int, rules_path: Path | None = None) -> bool:
    doc = load_rules(rules_path)
    rules = doc.get("rules") or []
    if index < 0 or index >= len(rules):
        return False
    rules.pop(index)
    doc["rules"] = rules
    save_rules(doc, rules_path)
    return True
