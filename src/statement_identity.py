"""Conservative issuer and card-product detection for newly uploaded files."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class StatementIdentity:
    issuer: str | None
    product: str | None
    confidence: str
    message: str

    @property
    def needs_manual_details(self) -> bool:
        return self.confidence != "detected"


def _detected(issuer: str, product: str | None, message: str) -> StatementIdentity:
    return StatementIdentity(issuer=issuer, product=product, confidence="detected", message=message)


def _manual(message: str) -> StatementIdentity:
    return StatementIdentity(issuer=None, product=None, confidence="manual", message=message)


def _csv_identity(path: Path) -> StatementIdentity:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            headers = {" ".join(value.split()).lower() for value in next(csv.reader(source), [])}
    except (OSError, UnicodeDecodeError, csv.Error):
        return _manual("Could not read CSV headers. Select its issuer before uploading.")

    if {"post date", "transaction date", "description", "amount"}.issubset(headers):
        return _detected("Chase", None, "Detected Chase CSV from its export headers.")
    if {"date", "description", "card member", "account #", "amount"}.issubset(headers):
        return StatementIdentity(
            issuer="American Express",
            product=None,
            confidence="product_required",
            message="Detected American Express CSV. Select the card product because this export omits it.",
        )
    return _manual("This CSV format is not recognized. Select an issuer to continue.")


def _product(text: str, issuer: str) -> str | None:
    lower = text.lower()
    if issuer == "Chase":
        for marker, product in (
            ("sapphire reserve", "Sapphire Reserve"),
            ("sapphire preferred", "Sapphire Preferred"),
            ("prime visa", "Amazon Prime Visa"),
        ):
            if marker in lower:
                return product
    if issuer == "Capital One":
        match = re.search(r"([A-Za-z0-9 ]+?)\s+Credit Card\s*\|", text)
        if match:
            return " ".join(match.group(1).split()).removeprefix("Capital One ").strip() or None
    if issuer == "Wells Fargo":
        match = re.search(r"WELLS FARGO\s+(.+?)\s+CARD", text, re.IGNORECASE)
        if match:
            return " ".join(match.group(1).split()).replace("®", "").title()
    if issuer == "American Express":
        for marker, product in (
            ("platinum card", "Platinum"),
            ("gold card", "Gold"),
            ("delta skymiles", "Delta SkyMiles"),
            ("blue cash", "Blue Cash"),
        ):
            if marker in lower:
                return product
    return None


# Multi-word brands are strong. Short tokens like "chase" are weak because they
# also appear as merchants inside other issuers' statements.
_PDF_ISSUERS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("American Express", (r"\bamerican express\b",), "strong"),
    ("Bank of America", (r"\bbank of america\b",), "strong"),
    ("Capital One", (r"\bcapital one\b",), "strong"),
    ("Wells Fargo", (r"\bwells fargo\b",), "strong"),
    ("Chase", (r"\bchase\b",), "weak"),
)

_FILENAME_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("American Express", ("americanexpress", "amex")),
    ("Bank of America", ("bankofamerica", "boa")),
    ("Capital One", ("capitalone", "capital1")),
    ("Wells Fargo", ("wellsfargo",)),
    ("Chase", ("chase",)),
)


def _issuer_hits(text: str) -> list[tuple[str, str]]:
    """Return ``(issuer, strength)`` pairs found in ``text``."""
    lower = text.lower()
    hits: list[tuple[str, str]] = []
    for issuer, patterns, strength in _PDF_ISSUERS:
        if any(re.search(pattern, lower) for pattern in patterns):
            hits.append((issuer, strength))
    return hits


def _choose_issuer(hits: list[tuple[str, str]]) -> str | None:
    if not hits:
        return None
    strong = [issuer for issuer, strength in hits if strength == "strong"]
    if len(strong) == 1:
        return strong[0]
    if len(strong) > 1:
        return None
    weak = [issuer for issuer, strength in hits if strength == "weak"]
    if len(weak) == 1:
        return weak[0]
    return None


def _filename_issuer(path: Path) -> str | None:
    stem = re.sub(r"[^a-z0-9]+", "", path.stem.lower())
    matches = [
        issuer
        for issuer, tokens in _FILENAME_HINTS
        if any(stem == token or stem.startswith(token) for token in tokens)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _pdf_identity(path: Path) -> StatementIdentity:
    try:
        with pdfplumber.open(path) as pdf:
            pages = [(page.extract_text() or "") for page in pdf.pages[:3]]
    except Exception:  # noqa: BLE001 — user-facing detection, not statement parsing
        return _manual("This PDF could not be read automatically. Select its issuer to continue.")

    header = pages[0] if pages else ""
    body = "\n".join(pages)
    if not body.strip():
        filename_issuer = _filename_issuer(path)
        if filename_issuer:
            return _detected(
                filename_issuer,
                None,
                f"Detected {filename_issuer} from the filename because the PDF had no extractable text.",
            )
        return _manual("This PDF has no extractable text. Select its issuer to continue.")

    issuer = _choose_issuer(_issuer_hits(header))
    source = "the statement header"
    if issuer is None:
        # Ignore weak tokens in later pages — merchant lines often include "CHASE".
        strong_body = [(name, strength) for name, strength in _issuer_hits(body) if strength == "strong"]
        issuer = _choose_issuer(strong_body)
        source = "the statement text"
    if issuer is None:
        filename_issuer = _filename_issuer(path)
        if filename_issuer:
            return _detected(
                filename_issuer,
                _product(body, filename_issuer),
                f"Detected {filename_issuer} from the filename because the statement text was ambiguous.",
            )
        return _manual("This PDF's issuer is ambiguous. Select it before uploading.")

    return _detected(issuer, _product(body, issuer), f"Detected {issuer} from {source}.")


def detect_statement_identity(path: Path) -> StatementIdentity:
    """Identify a supported statement without guessing when signals conflict."""
    if path.suffix.lower() == ".pdf":
        return _pdf_identity(path)
    if path.suffix.lower() == ".csv":
        return _csv_identity(path)
    return _manual("Unsupported statement format.")
