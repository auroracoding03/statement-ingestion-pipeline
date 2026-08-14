"""Split ledger rows into charges, merchant returns, and monthly card payments.

Spend headlines are net of returns and never include card payments. Classification
is read-time only: no ledger column is written.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

PAYMENT_LABELS = frozenset(
    {
        "monthly payment",
        "monthly-payment",
        "cardpayment",
        "card payment",
    }
)

PAYMENT_MERCHANT_RE = re.compile(
    r"(?i)payment thank you|mobile payment|autopay|online payment|"
    r"credit card payment|payment received|web payment|\bpayment\b"
)


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return not str(value).strip() or str(value).strip().lower() in {"nan", "none"}


def _norm(value: Any) -> str:
    if _blank(value):
        return ""
    return " ".join(str(value).split()).casefold()


def _tag_values(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _has_payment_label(value: Any) -> bool:
    text = _norm(value).replace("_", " ")
    if not text:
        return False
    collapsed = text.replace(" ", "")
    if text in PAYMENT_LABELS or collapsed in PAYMENT_LABELS:
        return True
    return "monthly payment" in text or text == "cardpayment"


def is_payment_row(row: Any) -> bool:
    """True when a negative row is a card payment rather than a merchant return."""
    amount = float(row.get("amount") or 0)
    if amount >= 0:
        return False
    if _has_payment_label(row.get("category")) or _has_payment_label(row.get("subcategory")):
        return True
    for tag in _tag_values(row.get("tags")):
        if _has_payment_label(tag):
            return True
    merchant = " ".join(
        part
        for part in (
            str(row.get("canonical_merchant") or ""),
            str(row.get("normalized_merchant") or ""),
            str(row.get("raw_description") or ""),
        )
        if part
    )
    return bool(PAYMENT_MERCHANT_RE.search(merchant))


def payment_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    return frame.apply(is_payment_row, axis=1)


def charge_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    return frame["amount"] > 0


def return_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    return (frame["amount"] < 0) & ~payment_mask(frame)


def non_payment_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Charges and merchant returns only — the rows that belong in spend."""
    if frame.empty:
        return frame
    return frame.loc[~payment_mask(frame)].copy()


def _money(value: float) -> float:
    return round(float(value), 2)


def summarize_spend(frame: pd.DataFrame) -> dict:
    """Gross charges, returns, monthly payments, and net spend (gross minus returns)."""
    if frame.empty:
        return {
            "gross_charges": 0.0,
            "returns_total": 0.0,
            "payments_total": 0.0,
            "net_spend": 0.0,
            "charge_count": 0,
            "return_count": 0,
            "payment_count": 0,
        }
    charges = frame.loc[charge_mask(frame)]
    payments = frame.loc[payment_mask(frame)]
    refunds = frame.loc[return_mask(frame)]
    gross = _money(charges["amount"].sum()) if not charges.empty else 0.0
    returns_total = _money(abs(refunds["amount"].sum())) if not refunds.empty else 0.0
    payments_total = _money(abs(payments["amount"].sum())) if not payments.empty else 0.0
    return {
        "gross_charges": gross,
        "returns_total": returns_total,
        "payments_total": payments_total,
        "net_spend": _money(gross - returns_total),
        "charge_count": int(len(charges)),
        "return_count": int(len(refunds)),
        "payment_count": int(len(payments)),
    }
