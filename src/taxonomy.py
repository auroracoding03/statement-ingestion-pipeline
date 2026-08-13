"""Delete or retarget spend categories across ledger, rules, merchants, and budget."""

from __future__ import annotations

from src import paths
from src.budget import load_budget, save_budget
from src.classify import drop_vocab, list_subcategories, load_rules, rewrite_rule_vocab
from src.merchants import load_merchants, save_merchants
from src.recurring import load_expected, save_expected

FALLBACK_CATEGORY = "Uncategorized"


def _norm(value) -> str:
    return " ".join(str(value or "").split()).strip()


def _bill_matches(bill: dict, category: str, subcategory: str | None) -> bool:
    if _norm(bill.get("category")) != category:
        return False
    if subcategory is None:
        return True
    return _norm(bill.get("subcategory")) == subcategory


def _merchant_matches(entry: dict, category: str, subcategory: str | None) -> bool:
    if _norm(entry.get("category")) != category:
        return False
    if subcategory is None:
        return True
    return _norm(entry.get("subcategory")) == subcategory


def _ledger_count(ledger, category: str, subcategory: str | None) -> int:
    if ledger is None or getattr(ledger, "empty", True):
        return 0
    cats = ledger["category"].fillna("").astype(str).str.strip()
    if subcategory is None:
        return int((cats == category).sum())
    subs = ledger["subcategory"].fillna("").astype(str).str.strip()
    return int(((cats == category) & (subs == subcategory)).sum())


def _rule_matches(rule: dict, category: str, subcategory: str | None) -> bool:
    if _norm(rule.get("category")) != category:
        return False
    if subcategory is None:
        return True
    return _norm(rule.get("subcategory")) == subcategory


def category_impact(ledger, category: str, subcategory: str | None = None) -> dict:
    cleaned = _norm(category)
    cleaned_sub = _norm(subcategory) or None
    rules = [rule for rule in (load_rules().get("rules") or []) if isinstance(rule, dict)]
    merchants = [entry for entry in (load_merchants().get("merchants") or []) if isinstance(entry, dict)]
    bills = load_expected()
    return {
        "category": cleaned,
        "subcategory": cleaned_sub,
        "txn_count": _ledger_count(ledger, cleaned, cleaned_sub),
        "rule_count": sum(1 for rule in rules if _rule_matches(rule, cleaned, cleaned_sub)),
        "merchant_count": sum(1 for entry in merchants if _merchant_matches(entry, cleaned, cleaned_sub)),
        "bill_count": sum(1 for bill in bills if _bill_matches(bill, cleaned, cleaned_sub)),
    }


def _rewrite_merchants(category: str, subcategory: str | None, *, action: str, reassign_category: str, reassign_subcategory: str) -> int:
    doc = load_merchants()
    entries = doc.get("merchants") or []
    changed = 0
    for entry in entries:
        if not isinstance(entry, dict) or not _merchant_matches(entry, category, subcategory):
            continue
        changed += 1
        if action == "reassign":
            entry["category"] = reassign_category
            if reassign_subcategory:
                entry["subcategory"] = reassign_subcategory
            else:
                entry.pop("subcategory", None)
        else:
            entry.pop("category", None)
            entry.pop("subcategory", None)
    if changed:
        doc["merchants"] = entries
        save_merchants(doc)
    return changed


def _rewrite_bills(category: str, subcategory: str | None, *, action: str, reassign_category: str, reassign_subcategory: str) -> int:
    if not paths.EXPECTED_RECURRING_PATH.exists():
        return 0
    bills = load_expected()
    changed = 0
    for bill in bills:
        if not isinstance(bill, dict) or not _bill_matches(bill, category, subcategory):
            continue
        changed += 1
        if action == "reassign":
            bill["category"] = reassign_category
            bill["subcategory"] = reassign_subcategory or None
        else:
            bill["category"] = None
            bill["subcategory"] = None
    if changed:
        save_expected(bills)
    return changed


def _rewrite_budget(category: str, subcategory: str | None, *, action: str, reassign_category: str, reassign_subcategory: str) -> None:
    doc = load_budget()
    envelopes = doc.get("envelopes") or []
    next_envelopes: list[dict] = []
    for env in envelopes:
        if env.get("category") != category:
            next_envelopes.append(env)
            continue
        if subcategory is None:
            if action == "reassign":
                target_exists = any(
                    item.get("category") == reassign_category for item in next_envelopes
                ) or any(
                    item.get("category") == reassign_category and item.get("category") != category
                    for item in envelopes
                )
                if not target_exists:
                    env = dict(env)
                    env["category"] = reassign_category
                    next_envelopes.append(env)
            continue
        subs = []
        for sub in env.get("subcategories") or []:
            if sub.get("subcategory") != subcategory:
                subs.append(sub)
                continue
            if action == "reassign" and reassign_category == category:
                updated = dict(sub)
                updated["subcategory"] = reassign_subcategory
                if reassign_subcategory:
                    subs.append(updated)
        env = dict(env)
        env["subcategories"] = subs
        next_envelopes.append(env)
    save_budget({"envelopes": next_envelopes})


def delete_category(
    ledger,
    category: str,
    subcategory: str | None = None,
    *,
    action: str,
    reassign_category: str = "",
    reassign_subcategory: str = "",
) -> dict:
    cleaned = _norm(category)
    cleaned_sub = _norm(subcategory) or None
    if not cleaned:
        raise ValueError("Category name is required")
    if cleaned == FALLBACK_CATEGORY and cleaned_sub is None:
        raise ValueError("Uncategorized cannot be deleted")
    if action not in {"unassign", "reassign"}:
        raise ValueError("action must be unassign or reassign")

    vocab = list_subcategories()
    primaries = list(load_rules().get("categories") or [])
    if cleaned_sub is None:
        if cleaned not in primaries and cleaned not in vocab:
            raise KeyError(cleaned)
    else:
        if cleaned_sub not in (vocab.get(cleaned) or []):
            raise KeyError(f"{cleaned}/{cleaned_sub}")

    reassign_cat = _norm(reassign_category)
    reassign_sub = _norm(reassign_subcategory)
    if action == "reassign":
        if not reassign_cat:
            raise ValueError("reassign_category is required")
        if reassign_cat == FALLBACK_CATEGORY and cleaned_sub is None:
            raise ValueError("Reassign to a real category, or choose Delete and unassign")
        if cleaned_sub is None and reassign_cat == cleaned:
            raise ValueError("Pick a different category")
        if cleaned_sub is not None and reassign_cat == cleaned and reassign_sub == cleaned_sub:
            raise ValueError("Pick a different subcategory")
        allowed = list_subcategories()
        if reassign_cat not in allowed and reassign_cat not in (load_rules().get("categories") or []):
            raise ValueError(f"Unknown category: {reassign_cat}")
        if reassign_sub and reassign_sub not in (allowed.get(reassign_cat) or []):
            raise ValueError(f"Unknown subcategory: {reassign_cat}/{reassign_sub}")

    impact = category_impact(ledger, cleaned, cleaned_sub)
    from src import pipeline

    rewritten = pipeline.rewrite_ledger_category(
        cleaned,
        cleaned_sub,
        action=action,
        reassign_category=reassign_cat,
        reassign_subcategory=reassign_sub,
    )
    rewrite_rule_vocab(
        cleaned,
        cleaned_sub,
        action=action,
        reassign_category=reassign_cat,
        reassign_subcategory=reassign_sub,
    )
    _rewrite_merchants(
        cleaned,
        cleaned_sub,
        action=action,
        reassign_category=reassign_cat,
        reassign_subcategory=reassign_sub,
    )
    _rewrite_bills(
        cleaned,
        cleaned_sub,
        action=action,
        reassign_category=reassign_cat,
        reassign_subcategory=reassign_sub,
    )
    _rewrite_budget(
        cleaned,
        cleaned_sub,
        action=action,
        reassign_category=reassign_cat,
        reassign_subcategory=reassign_sub,
    )
    drop_vocab(cleaned, cleaned_sub)
    return {**impact, "action": action, "rewritten": rewritten}
