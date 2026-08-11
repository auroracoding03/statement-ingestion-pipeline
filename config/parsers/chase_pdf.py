"""Native-text Chase consumer credit-card statement parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pdfplumber
import pandas as pd

from .base import coerce_amount, finalize, resolve_cycle_date

ISSUER = "Chase"
PERIOD_RE = re.compile(r"(?P<start>\d{2}/\d{2}/\d{2})\s*-\s*(?P<end>\d{2}/\d{2}/\d{2})")
DATE_RE = re.compile(r"^\d{2}/\d{2}$")
AMOUNT_RE = re.compile(r"[+-]?\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?")
NAME_RE = re.compile(r"^[A-Z][A-Z'-]+(?:\s+[A-Z][A-Z'-]+){1,3}$")

SECTION_LABELS = {
    "payments and other credits": "payments",
    "purchase": "purchases",
    "purchases": "purchases",
    "fees charged": "fees",
}
NON_ACTIVITY_HEADINGS = {
    "purchases and redemptions",
    "interest charges",
    "rewards activity",
    "account activity",
    "account activity continued",
}
NON_NAME_WORDS = {
    "ACCOUNT", "ACTIVITY", "ADVANCES", "AMAZON", "BALANCE", "CASH", "CHASE", "CARDMEMBER",
    "CREDITS", "FEES", "IMPORTANT", "INTEREST", "MESSAGES", "NEWS", "ORDER", "PAYMENTS",
    "POINTS", "PRIME", "PURCHASE", "PURCHASES", "REWARDS", "SERVICE", "SHOP", "SUMMARY",
    "TRANSFERS", "VISA", "YOUR",
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
    date_end: float
    description_end: float


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _collapse_doubled_glyphs(text: str) -> str:
    """Undo Chase PDF extractions that emit each letter twice (ACCOUNT → AACCCCOOUUUNNTT).

    Intentional double letters become quadruples under that encoding, so runs are
    halved (CCCC→CC) rather than fully deduped. Ordinary names like ALEX stay
    untouched because their consecutive-duplication density stays low.
    """
    raw = str(text or "")
    letters = [ch for ch in raw if ch.isalpha()]
    if len(letters) < 4:
        return raw
    dups = sum(1 for a, b in zip(letters, letters[1:]) if a.lower() == b.lower())
    if dups / (len(letters) - 1) < 0.35:
        return raw
    out: list[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if not ch.isalpha():
            out.append(ch)
            i += 1
            continue
        j = i + 1
        while j < len(raw) and raw[j].isalpha() and raw[j].lower() == ch.lower():
            j += 1
        keep = max(1, (j - i) // 2)
        out.extend(raw[i : i + keep])
        i = j
    return "".join(out)


def _lines(words: Iterable[dict[str, Any]], tolerance: float = 2.5) -> list[Line]:
    positioned = sorted(
        (
            Word(_collapse_doubled_glyphs(str(word["text"])), float(word["x0"]), float(word["top"]))
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


def _bounds(line: Line) -> Bounds | None:
    positions: dict[str, float] = {}
    for word in line.words:
        positions.setdefault(word.text.lower().rstrip(":"), word.x0)
    date_x = positions.get("date", positions.get("transaction"))
    merchant_x = positions.get("merchant")
    # Chase prints a "$ Amount" heading but right-aligns the numeric values to
    # the left edge of the dollar-sign column, not the word "Amount".
    amount_x = positions.get("$", positions.get("amount"))
    if date_x is None or merchant_x is None or amount_x is None or not date_x < merchant_x < amount_x:
        return None
    return Bounds(date_end=(date_x + merchant_x) / 2, description_end=amount_x - 8)


def _activity_heading(text: str) -> str | None:
    clean = _clean(text).lower()
    if clean in NON_ACTIVITY_HEADINGS:
        return "none"
    return SECTION_LABELS.get(clean)


def _statement_product(text: str) -> str | None:
    lower = text.lower()
    if "prime visa" in lower or ("amazon" in lower and "chase" in lower):
        return "Amazon Prime Visa"
    if "sapphire reserve" in lower:
        return "Sapphire Reserve"
    if "sapphire preferred" in lower:
        return "Sapphire Preferred"
    if "sapphire" in lower:
        return "Sapphire"
    return None


def _cardholder(text: str) -> str | None:
    clean = _clean(text)
    if not NAME_RE.fullmatch(clean):
        return None
    words = set(clean.split())
    if words & NON_NAME_WORDS:
        return None
    return clean.title()


def _resolve_date(value: str, start: date, end: date) -> date | None:
    parsed = datetime.strptime(value, "%m/%d")
    return resolve_cycle_date(parsed.month, parsed.day, start, end)


def _parse_row(line: Line, bounds: Bounds, start: date, end: date, section: str, metadata: dict[str, str | None], cardholder: str | None) -> dict[str, Any] | None:
    columns: list[list[Word]] = [[], [], []]
    for word in line.words:
        if word.x0 < bounds.date_end:
            columns[0].append(word)
        elif word.x0 < bounds.description_end:
            columns[1].append(word)
        else:
            columns[2].append(word)
    date_text, description, amount_text = (" ".join(word.text for word in column).strip() for column in columns)
    if not DATE_RE.fullmatch(date_text) or not description:
        return None
    amounts = AMOUNT_RE.findall(amount_text)
    if not amounts:
        return None
    posted_date = _resolve_date(date_text, start, end)
    if posted_date is None:
        # Far outside the cycle (and grace window): skip rather than fail the statement.
        return None
    return {
        "posted_date": posted_date,
        "amount": coerce_amount(amounts[-1]),
        "raw_description": description,
        "card_issuer": ISSUER,
        "card_product": metadata["card_product"],
        "cardholder": cardholder,
        "_section": section,
    }


def _parse_pages(pages: Iterable[Any], card: str, source_file: str) -> pd.DataFrame:
    page_lines: list[list[Line]] = []
    for page in pages:
        words = page.extract_words() or []
        page_lines.append(_lines(words))
    if not any(page_lines):
        raise ValueError("Chase statement contains no extractable text")

    all_lines = [line for lines in page_lines for line in lines]
    metadata: dict[str, str | None] = {"card_issuer": None, "card_product": None}
    start: date | None = None
    end: date | None = None
    holders: list[str] = []
    for line in all_lines:
        text = line.text
        if "chase" in text.lower():
            metadata["card_issuer"] = ISSUER
        metadata["card_product"] = _statement_product(text) or metadata["card_product"]
        period = PERIOD_RE.search(text)
        if period:
            start = datetime.strptime(period.group("start"), "%m/%d/%y").date()
            end = datetime.strptime(period.group("end"), "%m/%d/%y").date()
        holder = _cardholder(text)
        if holder and holder not in holders:
            holders.append(holder)

    if metadata["card_issuer"] != ISSUER:
        raise ValueError("Chase statement identity was not found")
    if not metadata["card_product"]:
        raise ValueError("Chase card product was not found")
    if start is None or end is None:
        raise ValueError("Chase statement period was not found")
    if len(holders) > 1:
        raise ValueError(f"Chase statement has ambiguous cardholders: {holders}")
    cardholder = holders[0] if holders else None

    rows: list[dict[str, Any]] = []
    section = "none"
    bounds: Bounds | None = None
    found_table = False
    for line in all_lines:
        text = _clean(line.text)
        heading = _activity_heading(text)
        if heading is not None:
            section = heading
            continue
        header = _bounds(line)
        if header:
            bounds = header
            found_table = True
            continue
        if section == "none" or bounds is None:
            continue
        row = _parse_row(line, bounds, start, end, section, metadata, cardholder)
        if row:
            rows.append(row)
            continue
        # Chase Amazon activity carries its order number on the following visual
        # line. Preserve it as source text while excluding unrelated narrative.
        if rows and text.lower().startswith("order number "):
            rows[-1]["raw_description"] = f"{rows[-1]['raw_description']} {text}"

    if not found_table:
        raise ValueError("Chase activity table was not found")
    if not rows:
        raise ValueError("Chase activity table contained no activity rows")
    for row in rows:
        row.pop("_section", None)
    return finalize(rows, card=card, source_file=source_file, metadata=metadata)


def parse_chase_pdf(path: Path, card: str, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    """Parse a native-text Chase statement; image-only PDFs are rejected."""
    with pdfplumber.open(path) as pdf:
        return _parse_pages(pdf.pages, card=card, source_file=str(path))
