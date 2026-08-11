"""Capital One consumer credit-card PDF parser.

Capital One statements contain a structured transaction table whose rows are
best reconstructed from positioned words rather than a whole-line regex. This
parser supports native-text PDFs only. Image-only PDFs fail deliberately so a
partial or OCR-corrupted statement never reaches the ledger.
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

from .base import coerce_amount, finalize, resolve_cycle_date

PARSER_ID = "capital_one_pdf_v1"
ISSUER = "Capital One"

CYCLE_RE = re.compile(
    r"(?P<start>[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*-\s*"
    r"(?P<end>[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})"
)
PRODUCT_RE = re.compile(r"(?P<product>[A-Za-z0-9 ]+?)\s+Credit Card\s*\|")
SHORT_DATE_RE = re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2}$")
AMOUNT_RE = re.compile(r"(?P<amount>[+-]?\s*\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")

SECTION_LABELS = {
    "payments, credits and adjustments": "payments",
    "transactions": "transactions",
    "fees": "fees",
    "interest charged": "interest",
    "cash advances": "cash_advances",
}
ACCOUNT_SUMMARY_LABELS = {
    "previous balance": "previous_balance",
    "payments": "payments",
    "other credits": "other_credits",
    "transactions": "transactions",
    "cash advances": "cash_advances",
    "fees charged": "fees",
    "interest charged": "interest",
    "new balance": "new_balance",
}


@dataclass(frozen=True)
class VisualWord:
    text: str
    x0: float
    top: float


@dataclass
class VisualLine:
    words: list[VisualWord]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words).strip()


@dataclass(frozen=True)
class ColumnBounds:
    trans_date_end: float
    post_date_end: float
    description_end: float


def _clean_text(text: str) -> str:
    return " ".join(str(text or "").split())


def _normalise_product(product: str) -> str:
    product = _clean_text(product)
    if product.lower().startswith("capital one "):
        product = product[len("capital one ") :]
    return product.title() if product.isupper() else product


def _normalise_cardholder(value: str) -> str | None:
    value = _clean_text(value).strip(" :-")
    if not value or not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,80}", value):
        return None
    return value.title() if value.isupper() else value


def _to_visual_words(words: Iterable[dict[str, Any]]) -> list[VisualWord]:
    return [
        VisualWord(str(word["text"]), float(word["x0"]), float(word["top"]))
        for word in words
        if str(word.get("text") or "").strip()
    ]


def _lines_from_words(words: Iterable[dict[str, Any]], y_tolerance: float = 2.5) -> list[VisualLine]:
    """Group positioned PDF words into visual rows."""
    ordered = sorted(_to_visual_words(words), key=lambda word: (word.top, word.x0))
    lines: list[VisualLine] = []
    current: list[VisualWord] = []
    baseline: float | None = None

    for word in ordered:
        if baseline is None or abs(word.top - baseline) <= y_tolerance:
            current.append(word)
            baseline = word.top if baseline is None else min(baseline, word.top)
            continue
        lines.append(VisualLine(sorted(current, key=lambda item: item.x0)))
        current = [word]
        baseline = word.top

    if current:
        lines.append(VisualLine(sorted(current, key=lambda item: item.x0)))
    return lines


def _column_bounds(line: VisualLine) -> ColumnBounds | None:
    """Build row-column cutoffs from the Capital One table headings."""
    positions: dict[str, float] = {}
    for word in line.words:
        key = word.text.lower().rstrip(":")
        if key in {"trans", "post", "description", "amount"}:
            positions.setdefault(key, word.x0)
    if not {"trans", "post", "description", "amount"}.issubset(positions):
        return None

    trans, post, description, amount = (
        positions["trans"],
        positions["post"],
        positions["description"],
        positions["amount"],
    )
    if not trans < post < description < amount:
        return None
    return ColumnBounds(
        trans_date_end=(trans + post) / 2,
        post_date_end=(post + description) / 2,
        # Descriptions can use nearly all of the whitespace before the right-
        # aligned Amount column, so the amount header itself is the useful
        # boundary rather than the midpoint between the two headers.
        description_end=amount - 8,
    )


def _last_amount(text: str) -> float | None:
    matches = list(AMOUNT_RE.finditer(text))
    if not matches:
        return None
    return coerce_amount(matches[-1].group("amount").replace(" ", ""))


def _infer_cycle_date(value: str, cycle_start: date, cycle_end: date) -> date | None:
    """Resolve Capital One's month/day rows onto the billing cycle (with grace)."""
    parsed = datetime.strptime(_clean_text(value), "%b %d")
    return resolve_cycle_date(parsed.month, parsed.day, cycle_start, cycle_end)


def _section_heading(text: str) -> tuple[str, str | None] | None:
    clean = _clean_text(text)
    lower = clean.lower()
    if "total " in lower or lower.startswith("totals "):
        return None
    for label, section in SECTION_LABELS.items():
        if not lower.endswith(label):
            continue
        prefix = clean[: len(clean) - len(label)].strip(" :-")
        return section, _normalise_cardholder(prefix)
    return None


def _is_total_line(text: str) -> bool:
    lower = _clean_text(text).lower()
    return lower.startswith("total ") or lower.startswith("totals ")


def _declared_total(text: str, section: str) -> float | None:
    lower = _clean_text(text).lower()
    expected = {
        "transactions": "total transactions",
        "fees": "total fees",
        "interest": "total interest",
        "cash_advances": "total cash advances",
    }.get(section)
    if expected and lower.startswith(expected):
        return _last_amount(text)
    return None


def _summary_value(text: str) -> tuple[str, float] | None:
    clean = _clean_text(text)
    lower = clean.lower()
    for label, key in ACCOUNT_SUMMARY_LABELS.items():
        if lower.startswith(label):
            amount = _last_amount(clean)
            if amount is not None:
                return key, amount
    return None


def _activity_amount(amount: float, section: str) -> float:
    if section == "payments":
        return -abs(amount)
    if section in {"fees", "interest", "cash_advances"}:
        return abs(amount)
    return amount


def _parse_table_row(
    line: VisualLine,
    bounds: ColumnBounds,
    cycle_start: date,
    cycle_end: date,
    section: str,
    cardholder: str | None,
    metadata: dict[str, str | None],
) -> dict[str, Any] | None:
    columns: list[list[VisualWord]] = [[], [], [], []]
    for word in line.words:
        if word.x0 < bounds.trans_date_end:
            columns[0].append(word)
        elif word.x0 < bounds.post_date_end:
            columns[1].append(word)
        elif word.x0 < bounds.description_end:
            columns[2].append(word)
        else:
            columns[3].append(word)

    trans_date, post_date, description, amount_text = (
        " ".join(word.text for word in column).strip() for column in columns
    )
    if not SHORT_DATE_RE.fullmatch(trans_date) or not SHORT_DATE_RE.fullmatch(post_date):
        return None
    if not description:
        return None
    amount = _last_amount(amount_text)
    if amount is None:
        return None

    posted = _infer_cycle_date(post_date, cycle_start, cycle_end)
    if posted is None:
        # Far outside the cycle (and grace window): skip rather than fail the statement.
        return None
    return {
        "posted_date": posted,
        "amount": _activity_amount(amount, section),
        "raw_description": description,
        "card_issuer": metadata["card_issuer"],
        "card_product": metadata["card_product"],
        "cardholder": cardholder,
        "_activity": section,
        "_transaction_date": _infer_cycle_date(trans_date, cycle_start, cycle_end),
    }


def _parse_interest_row(
    line: VisualLine,
    cycle_end: date,
    cardholder: str | None,
    metadata: dict[str, str | None],
) -> dict[str, Any] | None:
    text = _clean_text(line.text)
    if not text.lower().startswith("interest charge on "):
        return None
    amount = _last_amount(text)
    if amount is None or math.isclose(amount, 0.0, abs_tol=0.0001):
        return None
    description = AMOUNT_RE.sub("", text).strip(" -")
    return {
        "posted_date": cycle_end,
        "amount": abs(amount),
        "raw_description": description,
        "card_issuer": metadata["card_issuer"],
        "card_product": metadata["card_product"],
        "cardholder": cardholder,
        "_activity": "interest",
    }


def _validate_totals(rows: list[dict[str, Any]], declared: dict[str, float]) -> None:
    for section, expected in declared.items():
        actual = sum(float(row["amount"]) for row in rows if row.get("_activity") == section)
        if not math.isclose(actual, expected, abs_tol=0.01):
            raise ValueError(
                f"Capital One {section} total mismatch: extracted {actual:.2f}, statement {expected:.2f}"
            )


def _validate_account_summary(summary: dict[str, float]) -> None:
    required = {
        "previous_balance",
        "payments",
        "other_credits",
        "transactions",
        "cash_advances",
        "fees",
        "interest",
        "new_balance",
    }
    if not required.issubset(summary):
        return
    expected = (
        summary["previous_balance"]
        + summary["payments"]
        + summary["other_credits"]
        + summary["transactions"]
        + summary["cash_advances"]
        + summary["fees"]
        + summary["interest"]
    )
    if not math.isclose(expected, summary["new_balance"], abs_tol=0.01):
        raise ValueError(
            "Capital One account summary does not reconcile: "
            f"calculated {expected:.2f}, statement {summary['new_balance']:.2f}"
        )


def _parse_pages(pages: Iterable[Any], card: str, source_file: str) -> pd.DataFrame:
    """Parse native-text Capital One pages. Kept separate for focused tests."""
    lines: list[VisualLine] = []
    has_native_text = False
    for page in pages:
        words = page.extract_words() or []
        if words:
            has_native_text = True
            lines.extend(_lines_from_words(words))
    if not has_native_text:
        raise ValueError("Capital One statement contains no extractable text")

    metadata: dict[str, str | None] = {
        "card_issuer": None,
        "card_product": None,
    }
    cycle_start: date | None = None
    cycle_end: date | None = None
    account_summary: dict[str, float] = {}
    declared_totals: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    section = "none"
    cardholder: str | None = None
    bounds: ColumnBounds | None = None
    found_transaction_header = False

    for line in lines:
        text = _clean_text(line.text)
        if not text:
            continue
        if "capital one" in text.lower():
            metadata["card_issuer"] = ISSUER
        product_match = PRODUCT_RE.search(text)
        if product_match:
            metadata["card_product"] = _normalise_product(product_match.group("product"))
        cycle_match = CYCLE_RE.search(text)
        if cycle_match:
            cycle_start = datetime.strptime(cycle_match.group("start"), "%b %d, %Y").date()
            cycle_end = datetime.strptime(cycle_match.group("end"), "%b %d, %Y").date()

        summary = _summary_value(text)
        if summary:
            account_summary[summary[0]] = summary[1]

        heading = _section_heading(text)
        if heading:
            section, detected_holder = heading
            if detected_holder:
                cardholder = detected_holder
            continue

        header_bounds = _column_bounds(line)
        if header_bounds:
            bounds = header_bounds
            found_transaction_header = True
            continue

        if section == "none" or not bounds:
            continue
        if _is_total_line(text):
            total = _declared_total(text, section)
            if total is not None:
                declared_totals[section] = total
            continue

        if cycle_start is None or cycle_end is None:
            continue
        if section == "interest":
            row = _parse_interest_row(line, cycle_end, cardholder, metadata)
        else:
            row = _parse_table_row(
                line, bounds, cycle_start, cycle_end, section, cardholder, metadata
            )
        if row:
            rows.append(row)

    if metadata["card_issuer"] != ISSUER:
        raise ValueError("Capital One statement identity was not found")
    if not metadata["card_product"]:
        raise ValueError("Capital One card product was not found")
    if cycle_start is None or cycle_end is None:
        raise ValueError("Capital One statement period was not found")
    if not found_transaction_header:
        raise ValueError("Capital One transaction table was not found")
    if not rows:
        raise ValueError("Capital One transaction table contained no activity rows")

    _validate_totals(rows, declared_totals)
    _validate_account_summary(account_summary)
    for row in rows:
        row.pop("_activity", None)
        row.pop("_transaction_date", None)
    return finalize(rows, card=card, source_file=source_file, metadata=metadata)


def parse_capital_one_pdf(path: Path, card: str, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    """Parse a native-text Capital One consumer credit-card statement."""
    with pdfplumber.open(path) as pdf:
        return _parse_pages(pdf.pages, card=card, source_file=str(path))
