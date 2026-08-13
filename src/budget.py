"""Monthly spend envelopes over the existing rules.yaml categories."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml
from filelock import FileLock

from src import paths
from src.atomic import atomic_write_text
from src.classify import append_subcategory, list_subcategories, load_rules
from src.periods import Period, parse_iso_date


def _path(path: Path | None) -> Path:
    return path if path is not None else paths.BUDGET_PATH


def _money(value: float) -> float:
    return round(float(value), 2)


def _parse_amount(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount != amount:  # NaN
        return None
    return _money(amount)


def _normalize_sub(item: dict) -> dict | None:
    name = str(item.get("subcategory") or "").strip()
    if not name:
        return None
    return {
        "subcategory": name,
        "amount": _parse_amount(item.get("amount")),
        "show_on_overview": bool(item.get("show_on_overview")),
    }


def _normalize_category_envelope(item: dict) -> dict:
    category = str(item.get("category") or "").strip()
    subs: list[dict] = []
    seen: set[str] = set()
    for raw in item.get("subcategories") or []:
        if not isinstance(raw, dict):
            continue
        parsed = _normalize_sub(raw)
        if not parsed or parsed["subcategory"] in seen:
            continue
        seen.add(parsed["subcategory"])
        subs.append(parsed)
    return {
        "category": category,
        "amount": _parse_amount(item.get("amount")),
        "show_on_overview": bool(item.get("show_on_overview")),
        "subcategories": subs,
    }


def _sub_worth_saving(sub: dict) -> bool:
    return bool(str(sub.get("subcategory") or "").strip())


def _envelope_worth_saving(env: dict) -> bool:
    if env.get("amount") is not None or bool(env.get("show_on_overview")):
        return True
    return any(_sub_worth_saving(sub) for sub in env.get("subcategories") or [])


def load_budget(path: Path | None = None) -> dict:
    target = _path(path)
    if not target.exists():
        return {"envelopes": []}
    with target.open(encoding="utf-8") as handle:
        doc = yaml.safe_load(handle) or {}
    if not isinstance(doc, dict):
        return {"envelopes": []}
    envelopes = doc.get("envelopes") or []
    if not isinstance(envelopes, list):
        envelopes = []
    return {
        "envelopes": [
            _normalize_category_envelope(item) for item in envelopes if isinstance(item, dict) and str(item.get("category") or "").strip()
        ]
    }


def save_budget(data: dict, path: Path | None = None) -> dict:
    target = _path(path)
    envelopes: list[dict] = []
    for item in data.get("envelopes") or []:
        if not isinstance(item, dict):
            continue
        env = _normalize_category_envelope(item)
        if not env["category"] or not _envelope_worth_saving(env):
            continue
        env["subcategories"] = [sub for sub in env["subcategories"] if _sub_worth_saving(sub)]
        envelopes.append(env)
    doc = {"envelopes": envelopes}
    atomic_write_text(target, yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    return doc


def merge_envelopes(
    saved: list[dict] | None = None,
    *,
    categories: list[str] | None = None,
    subcategories: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Every primary from rules.yaml, with saved amounts overlaid."""
    cats = categories if categories is not None else list(load_rules().get("categories") or [])
    vocab = subcategories if subcategories is not None else list_subcategories()
    by_cat = {env["category"]: env for env in (saved or []) if env.get("category")}
    merged: list[dict] = []
    seen: set[str] = set()
    for name in cats:
        key = str(name).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        existing = by_cat.get(key) or {
            "category": key,
            "amount": None,
            "show_on_overview": False,
            "subcategories": [],
        }
        budgeted = {sub["subcategory"] for sub in existing["subcategories"]}
        available = [sub for sub in (vocab.get(key) or []) if sub not in budgeted]
        merged.append({**existing, "available_subcategories": available})
    for env in saved or []:
        key = env.get("category")
        if not key or key in seen:
            continue
        seen.add(key)
        budgeted = {sub["subcategory"] for sub in env["subcategories"]}
        available = [sub for sub in (vocab.get(key) or []) if sub not in budgeted]
        merged.append({**env, "available_subcategories": available})
    return merged


def list_budget(path: Path | None = None) -> list[dict]:
    return merge_envelopes(load_budget(path)["envelopes"])


def save_envelopes(envelopes: list[dict], path: Path | None = None) -> list[dict]:
    """Persist envelopes and append any new subcategory names to rules.yaml."""
    target = _path(path)
    vocab = list_subcategories()
    with FileLock(f"{target}.lock"):
        for env in envelopes:
            if not isinstance(env, dict):
                continue
            category = str(env.get("category") or "").strip()
            if not category:
                continue
            known = set(vocab.get(category) or [])
            for sub in env.get("subcategories") or []:
                if not isinstance(sub, dict):
                    continue
                name = str(sub.get("subcategory") or "").strip()
                if not name or name in known:
                    continue
                vocab = append_subcategory(category, name)
                known = set(vocab.get(category) or [])
        saved = save_budget({"envelopes": envelopes}, target)
    return merge_envelopes(saved["envelopes"])


def window_factor(period: Period) -> float:
    """How many monthly envelopes fit in this window.

    Calendar presets use inclusive month count. Custom windows prorate by days/30.
    """
    start = parse_iso_date(period.since)
    end = parse_iso_date(period.until)
    if start is None or end is None:
        return 1.0
    if period.preset == "custom":
        days = (end - start).days + 1
        return days / 30.0
    months = (end.year * 12 + end.month) - (start.year * 12 + start.month) + 1
    return float(max(months, 1))


def _is_uncategorized(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    text = str(value).strip()
    return text == "" or text == "Uncategorized"


def _blank_sub(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    return not str(value).strip()


def category_actuals(spend: pd.DataFrame) -> dict[str, float]:
    if spend.empty or "amount" not in spend.columns:
        return {}
    cats = spend["category"].where(~spend["category"].map(_is_uncategorized), "Uncategorized")
    grouped = spend.assign(category=cats).groupby("category", dropna=False)["amount"].sum()
    return {str(name): _money(total) for name, total in grouped.items()}


def subcategory_actuals(spend: pd.DataFrame) -> dict[tuple[str, str], float]:
    if spend.empty or "amount" not in spend.columns:
        return {}
    if "subcategory" not in spend.columns:
        return {}
    cats = spend["category"].where(~spend["category"].map(_is_uncategorized), "Uncategorized")
    frame = spend.assign(category=cats)
    frame = frame.loc[~frame["subcategory"].map(_blank_sub)]
    if frame.empty:
        return {}
    grouped = frame.groupby(["category", "subcategory"], dropna=False)["amount"].sum()
    return {(str(cat), str(sub)): _money(total) for (cat, sub), total in grouped.items()}


def budget_rows_for_period(
    spend: pd.DataFrame,
    period: Period,
    *,
    envelopes: list[dict] | None = None,
) -> list[dict]:
    """Overview rows for envelopes marked show_on_overview with a numeric amount."""
    saved = envelopes if envelopes is not None else load_budget()["envelopes"]
    factor = window_factor(period)
    cat_totals = category_actuals(spend)
    sub_totals = subcategory_actuals(spend)
    rows: list[dict] = []
    for env in saved:
        category = env["category"]
        if env.get("show_on_overview") and env.get("amount") is not None:
            budget = _money(float(env["amount"]) * factor)
            actual = cat_totals.get(category, 0.0)
            rows.append(
                {
                    "label": category,
                    "category": category,
                    "subcategory": None,
                    "budget": budget,
                    "actual": actual,
                    "variance": _money(actual - budget),
                }
            )
        for sub in env.get("subcategories") or []:
            if not (sub.get("show_on_overview") and sub.get("amount") is not None):
                continue
            name = sub["subcategory"]
            budget = _money(float(sub["amount"]) * factor)
            actual = sub_totals.get((category, name), 0.0)
            rows.append(
                {
                    "label": f"{category} / {name}",
                    "category": category,
                    "subcategory": name,
                    "budget": budget,
                    "actual": actual,
                    "variance": _money(actual - budget),
                }
            )
    return rows
