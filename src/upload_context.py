"""Per-upload context for statement formats that do not identify themselves."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.atomic import atomic_write_text

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ISSUER_ALIASES = {
    "amex": "American Express",
    "american express": "American Express",
    "capital one": "Capital One",
    "bank of america": "Bank of America",
    "boa": "Bank of America",
    "wells fargo": "Wells Fargo",
    "wf": "Wells Fargo",
    "chase": "Chase",
    "generic": "Generic",
}


def normalize_issuer(value: str) -> str:
    clean = " ".join(value.split()).lower()
    issuer = _ISSUER_ALIASES.get(clean)
    if issuer is None:
        raise ValueError(f"Unsupported issuer selection: {value!r}")
    return issuer


def normalize_product(value: str | None) -> str | None:
    if value is None:
        return None
    clean = " ".join(value.split())
    if len(clean) > 80:
        raise ValueError("Card product is too long")
    return clean or None


def card_key(issuer: str, product: str | None) -> str:
    """Return the inbox/ledger identifier while retaining the card product."""
    issuer_key = _SLUG_RE.sub("", issuer.lower())
    if not product:
        return issuer_key
    product_key = _SLUG_RE.sub("-", product.lower()).strip("-")
    return f"{issuer_key}-{product_key}" if product_key else issuer_key


def sidecar_path(statement: Path) -> Path:
    return statement.with_name(f".{statement.name}.upload.json")


def write_upload_context(statement: Path, *, issuer: str, product: str | None) -> None:
    context = {"card_issuer": issuer, "card_product": product}
    atomic_write_text(sidecar_path(statement), json.dumps(context, sort_keys=True) + "\n")


def read_upload_context(statement: Path) -> dict[str, Any]:
    context_path = sidecar_path(statement)
    if not context_path.exists():
        return {}
    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid upload context for {statement.name}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid upload context for {statement.name}")
    return data
