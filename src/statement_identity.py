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


def _pdf_identity(path: Path) -> StatementIdentity:
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages[:3])
    except Exception:  # noqa: BLE001 — user-facing detection, not statement parsing
        return _manual("This PDF could not be read automatically. Select its issuer to continue.")
    lower = text.lower()
    candidates = [
        ("American Express", ("american express", "member since")),
        ("Bank of America", ("bank of america",)),
        ("Capital One", ("capital one",)),
        ("Chase", ("chase",)),
        ("Wells Fargo", ("wells fargo",)),
    ]
    matches = [issuer for issuer, markers in candidates if any(marker in lower for marker in markers)]
    if len(matches) != 1:
        return _manual("This PDF's issuer is ambiguous. Select it before uploading.")
    issuer = matches[0]
    return _detected(issuer, _product(text, issuer), f"Detected {issuer} from the statement text.")


def detect_statement_identity(path: Path) -> StatementIdentity:
    """Identify a supported statement without guessing when signals conflict."""
    if path.suffix.lower() == ".pdf":
        return _pdf_identity(path)
    if path.suffix.lower() == ".csv":
        return _csv_identity(path)
    return _manual("Unsupported statement format.")
