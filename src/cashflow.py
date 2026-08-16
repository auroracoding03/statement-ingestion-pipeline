"""Split ledger rows into charges, returns, payments, transfers, and income.

Spend headlines are net of returns and never include card payments, transfers,
or income. Classification is read-time only: no ledger column is written.
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

TRANSFER_CATEGORY = "transfers"
INCOME_CATEGORY = "income"
BANK_NAME_RE = re.compile(r"(?i)\b(checking|savings|debit|banking|money market)\b")
TRANSFER_MERCHANT_RE = re.compile(
    r"(?i)online transfer|"
    r"american express\s+ach|"
    r"chase credit crd|"
    r"capital one.{0,20}pmt|"
    r"autograph visa"
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


def _merchant_blob(row: Any) -> str:
    return " ".join(
        part
        for part in (
            str(row.get("canonical_merchant") or ""),
            str(row.get("normalized_merchant") or ""),
            str(row.get("raw_description") or ""),
        )
        if part
    )


def is_bank_row(row: Any) -> bool:
    blob = f"{row.get('card_issuer') or ''} {row.get('card_product') or ''}"
    return bool(BANK_NAME_RE.search(blob))


def is_payment_row(row: Any) -> bool:
    """True when a negative card row is a monthly payment rather than a return."""
    amount = float(row.get("amount") or 0)
    if amount >= 0:
        return False
    if _norm(row.get("category")) == INCOME_CATEGORY:
        return False
    if is_bank_row(row):
        return False
    if _has_payment_label(row.get("category")) or _has_payment_label(row.get("subcategory")):
        return True
    for tag in _tag_values(row.get("tags")):
        if _has_payment_label(tag):
            return True
    return bool(PAYMENT_MERCHANT_RE.search(_merchant_blob(row)))


def is_transfer_row(row: Any) -> bool:
    """True for internal sweeps and bank-side card funding — not household spend."""
    if _norm(row.get("category")) == TRANSFER_CATEGORY:
        return True
    if _has_payment_label(row.get("subcategory")):
        return True
    return bool(TRANSFER_MERCHANT_RE.search(_merchant_blob(row)))


def is_income_row(row: Any) -> bool:
    return _norm(row.get("category")) == INCOME_CATEGORY


def payment_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    return frame.apply(is_payment_row, axis=1)


def transfer_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    return frame.apply(is_transfer_row, axis=1)


def income_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    return frame.apply(is_income_row, axis=1)


def bank_inflow_mask(frame: pd.DataFrame) -> pd.Series:
    """Bank inflows are never household expenses, even before Income classification."""
    if frame.empty:
        return pd.Series(dtype=bool)
    amounts = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
    return frame.apply(is_bank_row, axis=1) & (amounts < 0)


def charge_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    return frame["amount"] > 0


def return_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    return (frame["amount"] < 0) & ~payment_mask(frame) & ~income_mask(frame) & ~bank_inflow_mask(frame)


def non_payment_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Charges and merchant returns only — the rows that belong in spend."""
    if frame.empty:
        return frame
    return frame.loc[~payment_mask(frame)].copy()


def household_spend_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """True household expenses: card spend plus bank bills, minus transfers and income."""
    if frame.empty:
        return frame
    skip = payment_mask(frame) | transfer_mask(frame) | income_mask(frame) | bank_inflow_mask(frame)
    return frame.loc[~skip].copy()


def _money(value: float) -> float:
    return round(float(value), 2)


def income_total(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    income = frame.loc[income_mask(frame)]
    if income.empty:
        return 0.0
    return _money(float(income["amount"].abs().sum()))


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
            "income_total": 0.0,
            "surplus": 0.0,
            "bank_expenses": 0.0,
        }
    charges = frame.loc[charge_mask(frame)]
    payments = frame.loc[payment_mask(frame)]
    refunds = frame.loc[return_mask(frame)]
    gross = _money(charges["amount"].sum()) if not charges.empty else 0.0
    returns_total = _money(abs(refunds["amount"].sum())) if not refunds.empty else 0.0
    payments_total = _money(abs(payments["amount"].sum())) if not payments.empty else 0.0
    net = _money(gross - returns_total)
    inflow = income_total(frame)
    return {
        "gross_charges": gross,
        "returns_total": returns_total,
        "payments_total": payments_total,
        "net_spend": net,
        "charge_count": int(len(charges)),
        "return_count": int(len(refunds)),
        "payment_count": int(len(payments)),
        "income_total": inflow,
        "surplus": _money(inflow - net),
        "bank_expenses": 0.0,
    }


def summarize_household(frame: pd.DataFrame) -> dict:
    """Household spend plus income/surplus; card monthly payments stay on the full frame."""
    if frame.empty:
        return summarize_spend(frame)
    spend = household_spend_frame(frame)
    stats = summarize_spend(spend)
    pay = summarize_spend(frame)
    stats["payments_total"] = pay["payments_total"]
    stats["payment_count"] = pay["payment_count"]
    stats["income_total"] = income_total(frame)
    stats["surplus"] = _money(stats["income_total"] - stats["net_spend"])
    bank = spend.loc[spend.apply(is_bank_row, axis=1)] if not spend.empty else spend
    bank_charges = bank.loc[charge_mask(bank)] if not bank.empty else bank
    stats["bank_expenses"] = _money(bank_charges["amount"].sum()) if not bank_charges.empty else 0.0
    return stats
