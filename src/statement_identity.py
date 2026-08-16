"""Conservative issuer and card-product detection for newly uploaded files."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from src.upload_context import resolve_card_product_for_issuer

_NAME_LINE_RE = re.compile(r"^[A-Z][A-Z'-]*(?:\s+[A-Z][A-Z'-]*){1,3}$")
_NON_NAME_WORDS = {
    "ACCOUNT",
    "ACTIVITY",
    "AMERICAN",
    "AUTOGRAPH",
    "BALANCE",
    "BANK",
    "CARD",
    "CHASE",
    "CREDIT",
    "EXPRESS",
    "FARGO",
    "GOLD",
    "PLATINUM",
    "PREFERRED",
    "PRIME",
    "RESERVE",
    "REWARDS",
    "SAPPHIRE",
    "SIGNATURE",
    "STATEMENT",
    "SUMMARY",
    "VISA",
    "WELLS",
}


@dataclass(frozen=True)
class StatementIdentity:
    issuer: str | None
    product: str | None
    confidence: str
    message: str
    needs_cardholder: bool = False
    account_kind: str = "card"

    @property
    def needs_manual_details(self) -> bool:
        return self.confidence != "detected" or self.needs_cardholder

    def requiring_cardholder(self, extra: str | None = None) -> StatementIdentity:
        message = self.message
        if extra:
            suffix = extra.rstrip(".")
            if suffix.lower() not in message.lower():
                message = f"{message.rstrip('.')} — {suffix}."
        if self.needs_cardholder and message == self.message:
            return self
        return StatementIdentity(
            issuer=self.issuer,
            product=self.product,
            confidence=self.confidence,
            message=message,
            needs_cardholder=True,
            account_kind=self.account_kind,
        )


def _detected(issuer: str, product: str | None, message: str, *, account_kind: str = "card") -> StatementIdentity:
    return StatementIdentity(
        issuer=issuer, product=product, confidence="detected", message=message, account_kind=account_kind
    )


def _manual(message: str, *, account_kind: str = "card") -> StatementIdentity:
    return StatementIdentity(
        issuer=None, product=None, confidence="manual", message=message, account_kind=account_kind
    )


def _product_required(
    issuer: str, product: str | None, message: str, *, account_kind: str = "card"
) -> StatementIdentity:
    return StatementIdentity(
        issuer=issuer,
        product=product,
        confidence="product_required",
        message=message,
        account_kind=account_kind,
    )


def _finalize(issuer: str, product: str | None, message: str) -> StatementIdentity:
    """Apply vocabulary rules so invalid auto-products force a UI picker."""
    resolved, needs_selection = resolve_card_product_for_issuer(issuer, product)
    if needs_selection:
        hint = f" Detected label {product!r} is not a configured product." if product and product != resolved else ""
        return _product_required(
            issuer,
            None,
            f"{message.rstrip('.')} — select the card product to continue.{hint}".strip(),
        )
    return _detected(issuer, resolved, message)


def _header_index(headers: list[str], name: str) -> int | None:
    want = " ".join(name.split()).lower()
    for index, value in enumerate(headers):
        if " ".join(value.split()).lower() == want:
            return index
    return None


def _amex_csv_has_card_member(headers: list[str], rows: list[list[str]]) -> bool:
    index = _header_index(headers, "Card Member")
    if index is None:
        return False
    return any(index < len(row) and str(row[index]).strip() for row in rows)


def _pdf_has_cardholder_name(text: str) -> bool:
    for raw in text.splitlines():
        clean = " ".join(raw.split())
        if not _NAME_LINE_RE.fullmatch(clean):
            continue
        if set(clean.split()) & _NON_NAME_WORDS:
            continue
        return True
    return False


def _csv_identity(path: Path) -> StatementIdentity:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source)
            header_row = next(reader, [])
            data_rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error):
        return _manual("Could not read CSV headers. Select its issuer before uploading.")

    headers = {" ".join(value.split()).lower() for value in header_row}
    if {"post date", "transaction date", "description", "amount"}.issubset(headers):
        return _finalize("Chase", None, "Detected Chase CSV from its export headers.")
    if {"date", "description", "card member", "account #", "amount"}.issubset(headers):
        identity = _product_required(
            "American Express",
            None,
            "Detected American Express CSV. Select the card product because this export omits it.",
        )
        if not _amex_csv_has_card_member(header_row, data_rows):
            return identity.requiring_cardholder(
                "select the cardholder because this export has no Card Member names"
            )
        return identity
    if {"date", "description", "amount"}.issubset(headers) and (
        "status" in headers or "check #" in headers or "check number" in headers
    ):
        identity = _product_required(
            "Wells Fargo",
            None,
            "Detected Wells Fargo account history CSV. Select the account product because this export omits it.",
            account_kind="bank",
        )
        return identity.requiring_cardholder(
            "select the account holder because this export has no account nickname"
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


def _with_pdf_cardholder(identity: StatementIdentity, text: str) -> StatementIdentity:
    if _pdf_has_cardholder_name(text):
        return identity
    return identity.requiring_cardholder(
        "select the cardholder because no name was found on the statement"
    )


def _pdf_identity(path: Path) -> StatementIdentity:
    try:
        with pdfplumber.open(path) as pdf:
            pages = [(page.extract_text() or "") for page in pdf.pages[:3]]
    except Exception:  # noqa: BLE001 — user-facing detection, not statement parsing
        return _with_pdf_cardholder(
            _manual("This PDF could not be read automatically. Select its issuer to continue."),
            "",
        )

    header = pages[0] if pages else ""
    body = "\n".join(pages)
    if not body.strip():
        filename_issuer = _filename_issuer(path)
        if filename_issuer:
            return _with_pdf_cardholder(
                _finalize(
                    filename_issuer,
                    None,
                    f"Detected {filename_issuer} from the filename because the PDF had no extractable text.",
                ),
                body,
            )
        return _with_pdf_cardholder(
            _manual("This PDF has no extractable text. Select its issuer to continue."),
            body,
        )

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
            return _with_pdf_cardholder(
                _finalize(
                    filename_issuer,
                    _product(body, filename_issuer),
                    f"Detected {filename_issuer} from the filename because the statement text was ambiguous.",
                ),
                body,
            )
        return _with_pdf_cardholder(
            _manual("This PDF's issuer is ambiguous. Select it before uploading."),
            body,
        )

    return _with_pdf_cardholder(
        _finalize(issuer, _product(body, issuer), f"Detected {issuer} from {source}."),
        body,
    )


def detect_statement_identity(path: Path) -> StatementIdentity:
    """Identify a supported statement without guessing when signals conflict."""
    if path.suffix.lower() == ".pdf":
        return _pdf_identity(path)
    if path.suffix.lower() == ".csv":
        return _csv_identity(path)
    return _manual("Unsupported statement format.")
