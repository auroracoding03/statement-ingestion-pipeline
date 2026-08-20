"""Native-text Bank of America consumer credit-card statement parser.

BoA eStatements keep a positioned transaction table. Image-only or PDF24-
flattened copies have no extractable words and fail closed so they never
reach the ledger. CSV credit-card exports are not offered by BoA.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pdfplumber
import pandas as pd

from .base import coerce_amount, finalize, parse_month_day, resolve_cycle_date

ISSUER = "Bank of America"
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTH_ALT = "|".join(MONTHS)
PERIOD_RE = re.compile(
    rf"(?P<start_month>{MONTH_ALT})\s+(?P<start_day>\d{{1,2}})(?:,\s*(?P<start_year>\d{{4}}))?"
    rf"\s*(?:-|–|to)\s*"
    rf"(?P<end_month>{MONTH_ALT})\s+(?P<end_day>\d{{1,2}}),\s*(?P<end_year>\d{{4}})",
    re.I,
)
NUMERIC_PERIOD_RE = re.compile(
    r"(?P<start>\d{1,2}/\d{1,2}/\d{2,4})\s*(?:-|–|to)\s*(?P<end>\d{1,2}/\d{1,2}/\d{2,4})"
)
DATE_RE = re.compile(r"^\d{2}/\d{2}$")
AMOUNT_RE = re.compile(r"[+-]?\$?\d{1,3}(?:,\d{3})*\.\d{2}")
NAME_RE = re.compile(r"^[A-Z][A-Z'-]*(?:\s+[A-Z][A-Z'-]*){1,3}$")

PRODUCT_MARKERS = (
    ("customized cash rewards", "Customized Cash Rewards"),
    ("unlimited cash rewards", "Unlimited Cash Rewards"),
    ("premium rewards", "Premium Rewards"),
    ("travel rewards", "Travel Rewards"),
    ("air france", "Air France"),
    ("bankamericard", "BankAmericard"),
)
SECTION_LABELS = {
    "payments and other credits": "payments",
    "purchases and adjustments": "purchases",
    "fees charged": "fees",
    "interest charged": "interest",
}
STOP_HEADINGS = {
    "interest charge calculation",
    "important information about this account",
    "important messages",
    "your reward summary",
    "transactions",
}
NON_NAME_WORDS = {
    "ACCOUNT",
    "ACTIVITY",
    "ADJUSTMENTS",
    "AMERICA",
    "AMOUNT",
    "AVAILABLE",
    "BALANCE",
    "BANK",
    "CALCULATION",
    "CASH",
    "CHARGE",
    "CHARGED",
    "CLOSING",
    "CREDITS",
    "CYCLE",
    "DATE",
    "DESCRIPTION",
    "FEES",
    "INFORMATION",
    "INTEREST",
    "MASTERCARD",
    "NUMBER",
    "PAYMENTS",
    "PERIOD",
    "POSTING",
    "PURCHASES",
    "REFERENCE",
    "REWARDS",
    "STATEMENT",
    "SUMMARY",
    "TOTAL",
    "TRANSACTION",
    "TRANSACTIONS",
    "VISA",
}


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    top: float


@dataclass
class Line:
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words).strip()


@dataclass(frozen=True)
class Bounds:
    trans_end: float
    post_end: float
    description_end: float


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _lines(words: Iterable[dict[str, Any]], tolerance: float = 2.5) -> list[Line]:
    positioned = sorted(
        (
            Word(str(word["text"]), float(word["x0"]), float(word["top"]))
            for word in words
            if str(word.get("text") or "").strip()
        ),
        key=lambda word: (word.top, word.x0),
    )
    out: list[Line] = []
    current: list[Word] = []
    baseline: float | None = None
    for word in positioned:
        if baseline is None or abs(word.top - baseline) <= tolerance:
            current.append(word)
            baseline = word.top if baseline is None else min(baseline, word.top)
        else:
            out.append(Line(sorted(current, key=lambda item: item.x0)))
            current, baseline = [word], word.top
    if current:
        out.append(Line(sorted(current, key=lambda item: item.x0)))
    return out


def _month_number(value: str) -> int:
    return datetime.strptime(value[:3].title(), "%b").month


def _period_from_text(text: str) -> tuple[date, date] | None:
    named = PERIOD_RE.search(_clean(text))
    if named:
        end_year = int(named.group("end_year"))
        start_month = _month_number(named.group("start_month"))
        end_month = _month_number(named.group("end_month"))
        start_year = int(named.group("start_year") or 0) or (
            end_year - 1 if start_month > end_month else end_year
        )
        return (
            date(start_year, start_month, int(named.group("start_day"))),
            date(end_year, end_month, int(named.group("end_day"))),
        )
    numeric = NUMERIC_PERIOD_RE.search(_clean(text))
    if not numeric:
        return None
    start_raw, end_raw = numeric.group("start"), numeric.group("end")
    start_fmt = "%m/%d/%Y" if len(start_raw.split("/")[-1]) == 4 else "%m/%d/%y"
    end_fmt = "%m/%d/%Y" if len(end_raw.split("/")[-1]) == 4 else "%m/%d/%y"
    return datetime.strptime(start_raw, start_fmt).date(), datetime.strptime(end_raw, end_fmt).date()


def _product(text: str) -> str | None:
    from src.upload_context import is_generic_card_product

    lower = _clean(text).lower()
    for marker, product in PRODUCT_MARKERS:
        if marker in lower:
            return None if is_generic_card_product(product) else product
    return None


def _cardholder(text: str) -> str | None:
    clean = _clean(text)
    if not NAME_RE.fullmatch(clean) or set(clean.split()) & NON_NAME_WORDS:
        return None
    parts = clean.title().split()
    if len(parts) == 3 and len(parts[1]) == 1:
        parts.pop(1)
    return " ".join(parts)


def _column_bounds(line: Line) -> Bounds | None:
    positions: dict[str, float] = {}
    for word in line.words:
        positions.setdefault(word.text.lower().rstrip(":"), word.x0)
    trans = positions.get("transaction")
    post = positions.get("posting")
    description = positions.get("description")
    amount = positions.get("amount")
    if None in {trans, post, description, amount}:
        return None
    assert trans is not None and post is not None and description is not None and amount is not None
    if not trans < post < description < amount:
        return None
    reference = positions.get("reference")
    account = positions.get("account")
    cut = amount
    for edge in (reference, account):
        if edge is not None and description < edge < amount:
            cut = min(cut, edge)
    return Bounds(
        trans_end=(trans + post) / 2,
        post_end=(post + description) / 2,
        description_end=cut - 8,
    )


def _strip_amounts(text: str) -> str:
    return _clean(AMOUNT_RE.sub("", text)).strip(" -")


def _section_heading(text: str) -> str | None:
    lower = _strip_amounts(text).lower()
    if lower.startswith("total ") or "year-to-date" in lower:
        return None
    return SECTION_LABELS.get(lower)


def _is_stop_heading(text: str) -> bool:
    return _clean(text).lower() in STOP_HEADINGS


def _is_total_line(text: str) -> bool:
    lower = _clean(text).lower()
    return lower.startswith("total ") or "year-to-date" in lower


def _last_amount(text: str) -> float | None:
    matches = AMOUNT_RE.findall(text)
    if not matches:
        return None
    return coerce_amount(matches[-1])


def _activity_amount(amount: float, section: str) -> float:
    if section == "payments":
        return -abs(amount)
    if section in {"fees", "interest"}:
        return abs(amount)
    return amount


def _resolve_date(value: str, start: date, end: date) -> date | None:
    parsed = parse_month_day(value)
    if parsed is None:
        return None
    return resolve_cycle_date(parsed[0], parsed[1], start, end)


def _parse_row(
    line: Line,
    bounds: Bounds,
    start: date,
    end: date,
    section: str,
    cardholder: str | None,
    metadata: dict[str, str | None],
) -> dict[str, Any] | None:
    trans_words: list[Word] = []
    post_words: list[Word] = []
    description_words: list[Word] = []
    for word in line.words:
        if word.x0 < bounds.trans_end:
            trans_words.append(word)
        elif word.x0 < bounds.post_end:
            post_words.append(word)
        elif word.x0 < bounds.description_end:
            description_words.append(word)
    trans_date = " ".join(word.text for word in trans_words).strip()
    post_date = " ".join(word.text for word in post_words).strip()
    description = " ".join(word.text for word in description_words).strip()
    if not DATE_RE.fullmatch(trans_date) or not DATE_RE.fullmatch(post_date) or not description:
        return None
    amount = _last_amount(line.text)
    if amount is None or math.isclose(amount, 0.0, abs_tol=0.0001):
        return None
    posted = _resolve_date(post_date, start, end)
    if posted is None:
        return None
    return {
        "posted_date": posted,
        "amount": _activity_amount(amount, section),
        "raw_description": description,
        "card_issuer": ISSUER,
        "card_product": metadata["card_product"],
        "cardholder": cardholder,
    }


def _parse_pages(
    pages: Iterable[Any],
    card: str,
    source_file: str,
    upload_metadata: dict[str, Any] | None = None,
) -> pd.DataFrame:
    all_lines: list[Line] = []
    for page in pages:
        all_lines.extend(_lines(page.extract_words() or []))
    if not all_lines:
        raise ValueError("Bank of America statement contains no extractable text")

    upload_metadata = dict(upload_metadata or {})
    metadata: dict[str, str | None] = {
        "card_issuer": upload_metadata.get("card_issuer") or None,
        "card_product": upload_metadata.get("card_product") or None,
    }
    start: date | None = None
    end: date | None = None
    holders: list[str] = []
    for line in all_lines:
        text = line.text
        if "bank of america" in text.lower():
            metadata["card_issuer"] = ISSUER
        extracted = _product(text)
        if extracted and not metadata["card_product"]:
            metadata["card_product"] = extracted
        period = _period_from_text(text)
        if period:
            start, end = period
        holder = _cardholder(text)
        if holder and holder not in holders:
            holders.append(holder)
    if upload_metadata.get("card_product"):
        metadata["card_product"] = str(upload_metadata["card_product"])
    if metadata["card_issuer"] != ISSUER:
        raise ValueError("Bank of America statement identity was not found")
    if not metadata["card_product"]:
        raise ValueError("Bank of America card product was not found")
    if start is None or end is None:
        raise ValueError("Bank of America statement period was not found")
    if len(holders) > 1:
        raise ValueError(f"Bank of America statement has ambiguous cardholders: {holders}")

    bounds: Bounds | None = None
    rows: list[dict[str, Any]] = []
    section = "none"
    found_table = False
    for line in all_lines:
        text = _clean(line.text)
        if _is_stop_heading(text):
            if text.lower() != "transactions":
                section = "none"
            header = _column_bounds(line)
            if header:
                bounds = header
                found_table = True
            continue
        heading = _section_heading(text)
        if heading:
            section = heading
            continue
        header = _column_bounds(line)
        if header:
            bounds = header
            found_table = True
            continue
        if section == "none" or bounds is None or _is_total_line(text):
            continue
        row = _parse_row(line, bounds, start, end, section, holders[0] if holders else None, metadata)
        if row:
            rows.append(row)

    if not found_table:
        raise ValueError("Bank of America transaction table was not found")
    if not rows:
        raise ValueError("Bank of America transaction table contained no activity rows")
    return finalize(rows, card=card, source_file=source_file, metadata=metadata)


def parse_bank_of_america_pdf(path: Path, card: str, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    """Parse a native-text Bank of America credit-card statement."""
    with pdfplumber.open(path) as pdf:
        return _parse_pages(pdf.pages, card=card, source_file=str(path), upload_metadata=metadata)


def parse_bank_of_america_csv(path: Path, card: str, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    """BoA credit cards do not provide a CSV export."""
    product = (metadata or {}).get("card_product") or card
    raise ValueError(
        f"Bank of America credit-card statements are PDF-only; CSV is not supported for {product!r}"
    )
