"""Deterministic classification via ordered rules.yaml."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml

from src.paths import RULES_PATH


def load_rules(path: Path = RULES_PATH) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def save_rules(data: dict, path: Path = RULES_PATH) -> None:
    with path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def _compile_rules(rules_doc: dict) -> list[dict]:
    compiled: list[dict] = []
    for rule in rules_doc.get("rules") or []:
        match = rule.get("match") or {}
        pattern = match.get("merchant_regex") or match.get("description_regex")
        exact = match.get("merchant_exact")
        if not pattern and not exact:
            continue
        compiled.append(
            {
                "regex": re.compile(pattern) if pattern else None,
                "exact": (exact or "").upper() if exact else None,
                "category": rule.get("category", "Uncategorized"),
                "subcategory": rule.get("subcategory") or "",
            }
        )
    return compiled


def classify(frame: pd.DataFrame, rules_path: Path = RULES_PATH) -> pd.DataFrame:
    out = frame.copy()
    if "category" not in out.columns:
        out["category"] = None
        out["subcategory"] = None
        out["classified_by"] = None
        out["proposed_category"] = None
        out["proposed_subcategory"] = None

    rules = _compile_rules(load_rules(rules_path))
    categories: list[str | None] = []
    subcategories: list[str | None] = []
    classified_by: list[str | None] = []

    for _, row in out.iterrows():
        merchant = str(row.get("normalized_merchant") or "")
        raw = str(row.get("raw_description") or "")
        hit_cat = None
        hit_sub = None
        for rule in rules:
            matched = False
            if rule["exact"] and merchant == rule["exact"]:
                matched = True
            elif rule["regex"] and (rule["regex"].search(merchant) or rule["regex"].search(raw)):
                matched = True
            if matched:
                hit_cat = rule["category"]
                hit_sub = rule["subcategory"]
                break
        categories.append(hit_cat)
        subcategories.append(hit_sub)
        classified_by.append("rule" if hit_cat else None)

    out["category"] = categories
    out["subcategory"] = subcategories
    out["classified_by"] = classified_by
    return out


def append_rule(
    *,
    merchant_regex: str,
    category: str,
    subcategory: str = "",
    rules_path: Path = RULES_PATH,
) -> None:
    doc = load_rules(rules_path)
    rules = doc.setdefault("rules", [])
    rules.insert(
        0,
        {
            "match": {"merchant_regex": merchant_regex},
            "category": category,
            "subcategory": subcategory,
        },
    )
    # Keep category list in sync
    cats = doc.setdefault("categories", [])
    if category not in cats:
        cats.append(category)
    save_rules(doc, rules_path)
