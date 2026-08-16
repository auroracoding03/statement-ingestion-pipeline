"""Per-product statement coverage and spend stats for the Cards page."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re

import pandas as pd

from src.cashflow import household_spend_frame, summarize_household
from src.upload_context import list_card_products, normalize_cardholder

UNASSIGNED = "Unassigned"
GAP_DAYS = 14
STALE_DAYS = 40
BANK_NAME_RE = re.compile(r"(?i)\b(checking|savings|debit|banking|money market)\b")


def _money(value: float) -> float:
    return round(float(value), 2)


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return not str(value).strip() or str(value).strip().lower() in {"nan", "none"}


def _holder_blank(value) -> bool:
    if _blank(value):
        return True
    return str(value).strip().casefold() == UNASSIGNED.casefold()


def assign_cardholder(
    ledger: pd.DataFrame,
    *,
    issuer: str,
    product: str,
    cardholder: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Stamp a name onto blank/Unassigned rows for one issuer+product."""
    name = normalize_cardholder(cardholder)
    issuer_name = " ".join((issuer or "").split())
    product_name = " ".join((product or "").split())
    if not issuer_name or not product_name:
        raise ValueError("Issuer and card product are required")
    if ledger.empty or "cardholder" not in ledger.columns:
        return ledger, []

    issuers = ledger["card_issuer"].map(_label) if "card_issuer" in ledger.columns else pd.Series("", index=ledger.index)
    products = ledger["card_product"].map(_label) if "card_product" in ledger.columns else pd.Series("", index=ledger.index)
    match = (issuers == issuer_name) & (products == product_name) & ledger["cardholder"].map(_holder_blank)
    if not match.any():
        return ledger, []

    out = ledger.copy()
    out.loc[match, "cardholder"] = name
    txn_ids = []
    if "txn_id" in out.columns:
        txn_ids = [str(value) for value in out.loc[match, "txn_id"].tolist()]
    return out, txn_ids


def _label(value) -> str:
    if _blank(value):
        return ""
    return str(value).strip()


def _as_date(value) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _product_key(row: pd.Series) -> tuple[str, str, str]:
    issuer = _label(row.get("card_issuer"))
    product = _label(row.get("card_product"))
    holder = _label(row.get("cardholder")) or UNASSIGNED
    if not issuer and not product:
        return ("", UNASSIGNED, holder)
    return (issuer or "Unknown", product or UNASSIGNED, holder)


def _display_label(issuer: str, product: str, cardholder: str) -> str:
    base = f"{issuer} {product}".strip() or UNASSIGNED
    if cardholder:
        return f"{base} · {cardholder}"
    return base


def _document_key(row: pd.Series) -> str:
    doc = _label(row.get("source_document_id"))
    if doc:
        return doc
    source = _label(row.get("source_file"))
    return source or "unknown"


def _file_name(row: pd.Series) -> str:
    source = _label(row.get("source_file"))
    return Path(source).name if source else "Unknown statement"


def _is_uncategorized(value) -> bool:
    if _blank(value):
        return True
    return str(value).strip() == "Uncategorized"


def _status(statement_count: int, gaps: list[dict], stale_days: int | None) -> str:
    if statement_count == 0:
        return "none"
    if gaps:
        return "gap"
    if stale_days is not None:
        return "stale"
    return "ok"


def account_kind(issuer: str, product: str) -> str:
    blob = f"{issuer} {product}"
    return "bank" if BANK_NAME_RE.search(blob) else "card"


def _statement_summary(frame: pd.DataFrame) -> dict:
    posted = [_as_date(value) for value in frame["posted_date"]]
    posted = [value for value in posted if value]
    stats = summarize_household(frame)
    spend = household_spend_frame(frame)
    uncategorized = (
        spend[(spend["amount"] > 0) & spend["category"].map(_is_uncategorized)]
        if not spend.empty and "category" in spend.columns
        else frame.iloc[0:0]
    )
    start = min(posted) if posted else None
    end = max(posted) if posted else None
    return {
        "txn_count": int(len(frame)),
        "charge_count": stats["charge_count"],
        "spend_total": stats["net_spend"],
        "gross_charges": stats["gross_charges"],
        "returns_total": stats["returns_total"],
        "payments_total": stats["payments_total"],
        "income_total": stats["income_total"],
        "bank_expenses": stats["bank_expenses"],
        "uncategorized_count": int(len(uncategorized)),
        "uncategorized_total": _money(uncategorized["amount"].sum()) if not uncategorized.empty else 0.0,
        "first_posted": _iso(start),
        "last_posted": _iso(end),
        "coverage_start": start,
        "coverage_end": end,
    }


def _gaps(statements: list[dict]) -> list[dict]:
    ordered = [row for row in statements if row["coverage_start"] and row["coverage_end"]]
    ordered.sort(key=lambda row: (row["coverage_start"], row["coverage_end"]))
    gaps: list[dict] = []
    for prior, current in zip(ordered, ordered[1:]):
        delta = (current["coverage_start"] - prior["coverage_end"]).days
        if delta > GAP_DAYS:
            gaps.append(
                {
                    "after": prior["coverage_end"].isoformat(),
                    "before": current["coverage_start"].isoformat(),
                    "days": delta,
                }
            )
    return gaps


def _stale_days(statements: list[dict], today: date) -> int | None:
    ends = [row["coverage_end"] for row in statements if row["coverage_end"]]
    if not ends:
        return None
    age = (today - max(ends)).days
    return age if age > STALE_DAYS else None


def _empty_product(issuer: str, product: str, cardholder: str = "") -> dict:
    return {
        "issuer": issuer,
        "product": product,
        "cardholder": cardholder or None,
        "label": _display_label(issuer, product, cardholder),
        "account_kind": account_kind(issuer, product),
        "status": "none",
        "statement_count": 0,
        "charge_count": 0,
        "spend_total": 0.0,
        "gross_charges": 0.0,
        "returns_total": 0.0,
        "payments_total": 0.0,
        "income_total": 0.0,
        "bank_expenses": 0.0,
        "uncategorized_count": 0,
        "uncategorized_total": 0.0,
        "first_posted": None,
        "last_posted": None,
        "coverage_start": None,
        "coverage_end": None,
        "stale_days": None,
        "statements": [],
        "gaps": [],
    }


def _serialize_product(issuer: str, product: str, cardholder: str, frame: pd.DataFrame, today: date) -> dict:
    summary = _statement_summary(frame)
    statements: list[dict] = []
    if not frame.empty:
        grouped = frame.groupby(frame.apply(_document_key, axis=1), dropna=False)
        for key, chunk in grouped:
            stats = _statement_summary(chunk)
            statements.append(
                {
                    "id": str(key),
                    "file_name": _file_name(chunk.iloc[0]),
                    "txn_count": stats["txn_count"],
                    "spend_total": stats["spend_total"],
                    "gross_charges": stats["gross_charges"],
                    "returns_total": stats["returns_total"],
                    "payments_total": stats["payments_total"],
                    "income_total": stats["income_total"],
                    "bank_expenses": stats["bank_expenses"],
                    "coverage_start": _iso(stats["coverage_start"]),
                    "coverage_end": _iso(stats["coverage_end"]),
                }
            )
        statements.sort(key=lambda row: (row["coverage_start"] or "", row["file_name"]))
    dated = [
        {
            **row,
            "coverage_start": date.fromisoformat(row["coverage_start"]) if row["coverage_start"] else None,
            "coverage_end": date.fromisoformat(row["coverage_end"]) if row["coverage_end"] else None,
        }
        for row in statements
    ]
    gaps = _gaps(dated)
    stale = _stale_days(dated, today)
    return {
        "issuer": issuer,
        "product": product,
        "cardholder": cardholder or None,
        "label": _display_label(issuer, product, cardholder),
        "account_kind": account_kind(issuer, product),
        "status": _status(len(statements), gaps, stale),
        "statement_count": len(statements),
        "charge_count": summary["charge_count"],
        "spend_total": summary["spend_total"],
        "gross_charges": summary["gross_charges"],
        "returns_total": summary["returns_total"],
        "payments_total": summary["payments_total"],
        "income_total": summary["income_total"],
        "bank_expenses": summary["bank_expenses"],
        "uncategorized_count": summary["uncategorized_count"],
        "uncategorized_total": summary["uncategorized_total"],
        "first_posted": summary["first_posted"],
        "last_posted": summary["last_posted"],
        "coverage_start": _iso(summary["coverage_start"]),
        "coverage_end": _iso(summary["coverage_end"]),
        "stale_days": stale,
        "statements": statements,
        "gaps": gaps,
    }


def build_cards_coverage(
    ledger: pd.DataFrame,
    *,
    issuer: str | None = None,
    product: str | None = None,
    cardholder: str | None = None,
    today: date | None = None,
    configured: dict[str, list[str]] | None = None,
) -> dict:
    today = today or date.today()
    vocab = configured if configured is not None else list_card_products()
    products: dict[tuple[str, str, str], dict] = {}

    for issuer_name, names in (vocab or {}).items():
        for name in names or []:
            label = _label(name)
            if label:
                products[(issuer_name, label, "")] = _empty_product(issuer_name, label)

    seen_products: set[tuple[str, str]] = set()
    if not ledger.empty:
        keys = ledger.apply(_product_key, axis=1)
        for (issuer_name, product_name, holder), chunk in ledger.groupby(keys, sort=False):
            products[(issuer_name, product_name, holder)] = _serialize_product(
                issuer_name, product_name, holder, chunk, today
            )
            seen_products.add((issuer_name, product_name))

    for issuer_name, product_name in seen_products:
        products.pop((issuer_name, product_name, ""), None)

    ordered = sorted(products.values(), key=lambda row: (row["issuer"], row["product"], row["cardholder"] or ""))
    selected = None
    want_issuer = issuer.strip() if issuer and issuer.strip() else None
    want_product = product.strip() if product and product.strip() else None
    want_holder = cardholder.strip() if cardholder and cardholder.strip() else None
    if want_issuer or want_product or want_holder:
        for row in ordered:
            issuer_ok = want_issuer is None or row["issuer"] == want_issuer
            product_ok = want_product is None or row["product"] == want_product
            holder_ok = want_holder is None or (row["cardholder"] or "") == want_holder
            if issuer_ok and product_ok and holder_ok:
                selected = {
                    "issuer": row["issuer"],
                    "product": row["product"],
                    "cardholder": row["cardholder"],
                }
                break
    return {"products": ordered, "selected": selected}
