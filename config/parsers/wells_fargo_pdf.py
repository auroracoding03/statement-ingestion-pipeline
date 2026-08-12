"""Native-text Wells Fargo consumer credit-card statement parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pdfplumber
import pandas as pd

from .base import coerce_amount, finalize, parse_month_day, resolve_cycle_date

ISSUER = "Wells Fargo"
PERIOD_RE = re.compile(r"Statement Period\s+(?P<start>\d{2}/\d{2}/\d{4})\s+to\s+(?P<end>\d{2}/\d{2}/\d{4})", re.I)
DATE_RE = re.compile(r"^\d{2}/\d{2}$")
AMOUNT_RE = re.compile(r"\$?\d{1,3}(?:,\d{3})*\.\d{2}")
NAME_RE = re.compile(r"^[A-Z][A-Z'-]*(?:\s+[A-Z][A-Z'-]*){1,3}$")
PRODUCT_RE = re.compile(r"WELLS FARGO\s+(?P<product>.+?)\s+CARD", re.I)

SECTIONS = {
    "payments": "payments",
    "purchases, balance transfers & other charges": "purchases",
    "fees charged": "fees",
    "interest charged": "interest",
}
NON_NAME_WORDS = {
    "ACCOUNT", "AMOUNT", "AUTOGRAPH", "BALANCE", "CARD", "CHARGED", "CHARGES", "CREDITS",
    "FARGO", "FEES", "INTEREST", "PAYMENTS", "PURCHASES", "SIGNATURE", "TRANSACTIONS", "VISA", "WELLS",
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
    reference_end: float
    description_end: float
    credits_end: float


def _clean(value: str) -> str:
    return " ".join(str(value or "").split())


def _lines(words: Iterable[dict[str, Any]], tolerance: float = 2.5) -> list[Line]:
    positioned = sorted(
        (Word(str(word["text"]), float(word["x0"]), float(word["top"])) for word in words if str(word.get("text") or "").strip()),
        key=lambda word: (word.top, word.x0),
    )
    lines: list[Line] = []
    current: list[Word] = []
    baseline: float | None = None
    for word in positioned:
        if baseline is None or abs(word.top - baseline) <= tolerance:
            current.append(word)
            baseline = word.top if baseline is None else min(baseline, word.top)
        else:
            lines.append(Line(sorted(current, key=lambda item: item.x0)))
            current, baseline = [word], word.top
    if current:
        lines.append(Line(sorted(current, key=lambda item: item.x0)))
    return lines


def _column_bounds(line: Line) -> Bounds | None:
    positions: dict[str, float] = {}
    for word in line.words:
        positions.setdefault(word.text.lower().rstrip(":"), word.x0)
    trans = positions.get("trans")
    post = positions.get("post")
    reference = positions.get("reference")
    description = positions.get("description")
    credits = positions.get("credits")
    charges = positions.get("charges")
    if None in {trans, post, reference, description, credits, charges}:
        return None
    assert all(value is not None for value in (trans, post, reference, description, credits, charges))
    if not trans < post < reference < description < credits < charges:
        return None
    return Bounds(
        trans_end=(trans + post) / 2,
        post_end=(post + reference) / 2,
        reference_end=(reference + description) / 2,
        description_end=(description + credits) / 2,
        credits_end=(credits + charges) / 2,
    )


def _product(text: str) -> str | None:
    from src.upload_context import is_generic_card_product

    match = PRODUCT_RE.search(_clean(text))
    if not match:
        return None
    product = match.group("product").replace("®", "").strip().title()
    if is_generic_card_product(product):
        return None
    return product


def _cardholder(text: str) -> str | None:
    clean = _clean(text)
    if not NAME_RE.fullmatch(clean) or set(clean.split()) & NON_NAME_WORDS:
        return None
    parts = clean.title().split()
    # Statements may include a middle initial; ledger ownership is deliberately
    # limited to the cardholder's first and last name for cross-issuer matching.
    if len(parts) == 3 and len(parts[1]) == 1:
        parts.pop(1)
    return " ".join(parts)


def _resolve_date(value: str, start: date, end: date) -> date | None:
    parsed = parse_month_day(value)
    if parsed is None:
        return None
    return resolve_cycle_date(parsed[0], parsed[1], start, end)


def _amount(text: str, sign: int) -> float | None:
    matches = AMOUNT_RE.findall(text)
    if not matches:
        return None
    amount = coerce_amount(matches[-1])
    return sign * abs(amount)


def _non_money_text(text: str) -> str:
    return " ".join(token for token in text.split() if not AMOUNT_RE.fullmatch(token))


def _parse_row(line: Line, bounds: Bounds, start: date, end: date, cardholder: str | None, metadata: dict[str, str | None]) -> dict[str, Any] | None:
    columns: list[list[Word]] = [[], [], [], [], [], []]
    for word in line.words:
        if word.x0 < bounds.trans_end:
            columns[0].append(word)
        elif word.x0 < bounds.post_end:
            columns[1].append(word)
        elif word.x0 < bounds.reference_end:
            columns[2].append(word)
        elif word.x0 < bounds.description_end:
            columns[3].append(word)
        elif word.x0 < bounds.credits_end:
            columns[4].append(word)
        else:
            columns[5].append(word)
    trans_cell, post_date, _reference, description, credits, charges = (
        " ".join(word.text for word in column).strip() for column in columns
    )
    trans_dates = re.findall(r"\b\d{2}/\d{2}\b", trans_cell)
    if len(trans_dates) != 1 or not DATE_RE.fullmatch(post_date) or not description:
        return None
    credit = _amount(credits, -1)
    charge = _amount(charges, 1)
    leftover = " ".join(part for part in (_non_money_text(credits), _non_money_text(charges)) if part)
    if leftover:
        description = f"{description} {leftover}".strip()
    if credit is not None and charge is not None:
        raise ValueError(f"Wells Fargo row has both credit and charge amounts: {line.text}")
    amount = credit if credit is not None else charge
    if amount is None:
        return None
    posted_date = _resolve_date(post_date, start, end)
    if posted_date is None:
        # Far outside the cycle (and grace window): skip rather than fail the statement.
        return None
    return {
        "posted_date": posted_date,
        "amount": amount,
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
        raise ValueError("Wells Fargo statement contains no extractable text")

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
        if "wells fargo" in text.lower():
            metadata["card_issuer"] = ISSUER
        extracted = _product(text)
        if extracted and not metadata["card_product"]:
            metadata["card_product"] = extracted
        period = PERIOD_RE.search(text)
        if period:
            start = datetime.strptime(period.group("start"), "%m/%d/%Y").date()
            end = datetime.strptime(period.group("end"), "%m/%d/%Y").date()
        holder = _cardholder(text)
        if holder and holder not in holders:
            holders.append(holder)
    if upload_metadata.get("card_product"):
        metadata["card_product"] = str(upload_metadata["card_product"])
    if metadata["card_issuer"] != ISSUER:
        raise ValueError("Wells Fargo statement identity was not found")
    if not metadata["card_product"]:
        raise ValueError("Wells Fargo card product was not found")
    if start is None or end is None:
        raise ValueError("Wells Fargo statement period was not found")
    if len(holders) > 1:
        raise ValueError(f"Wells Fargo statement has ambiguous cardholders: {holders}")

    bounds: Bounds | None = None
    rows: list[dict[str, Any]] = []
    section = "none"
    found_table = False
    for line in all_lines:
        text = _clean(line.text)
        heading = SECTIONS.get(text.lower())
        if heading:
            section = heading
            continue
        header = _column_bounds(line)
        if header:
            bounds = header
            found_table = True
            continue
        if section == "none" or bounds is None:
            continue
        row = _parse_row(line, bounds, start, end, holders[0] if holders else None, metadata)
        if row:
            rows.append(row)

    if not found_table:
        raise ValueError("Wells Fargo transaction table was not found")
    if not rows:
        raise ValueError("Wells Fargo transaction table contained no activity rows")
    return finalize(rows, card=card, source_file=source_file, metadata=metadata)


def parse_wells_fargo_pdf(path: Path, card: str, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    """Parse native-text Wells Fargo credit-card statements only."""
    with pdfplumber.open(path) as pdf:
        return _parse_pages(pdf.pages, card=card, source_file=str(path), upload_metadata=metadata)
