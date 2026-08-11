"""Per-upload context for statement formats that do not identify themselves."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock

from src import paths
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
_KNOWN_ISSUERS = (
    "American Express",
    "Bank of America",
    "Capital One",
    "Chase",
    "Wells Fargo",
    "Generic",
)


def normalize_issuer(value: str) -> str:
    clean = " ".join(value.split()).lower()
    issuer = _ISSUER_ALIASES.get(clean)
    if issuer is None:
        raise ValueError(f"Unsupported issuer selection: {value!r}")
    return issuer


def _products_path(path: Path | None = None) -> Path:
    return path if path is not None else paths.CARD_PRODUCTS_PATH


def load_card_products(path: Path | None = None) -> dict:
    target = _products_path(path)
    if not target.exists():
        return {"products": {issuer: [] for issuer in _KNOWN_ISSUERS}}
    with target.open(encoding="utf-8") as handle:
        doc = yaml.safe_load(handle) or {}
    if not isinstance(doc, dict):
        return {"products": {issuer: [] for issuer in _KNOWN_ISSUERS}}
    raw = doc.get("products") or {}
    if not isinstance(raw, dict):
        raw = {}
    products: dict[str, list[str]] = {}
    for issuer in _KNOWN_ISSUERS:
        bucket = raw.get(issuer) or []
        items: list[str] = []
        if isinstance(bucket, list):
            for value in bucket:
                text = " ".join(str(value).split()).strip()
                if text and text not in items:
                    items.append(text)
        products[issuer] = items
    # Preserve any extra issuer keys that already exist in the file.
    for key, bucket in raw.items():
        issuer = str(key).strip()
        if not issuer or issuer in products:
            continue
        items = []
        if isinstance(bucket, list):
            for value in bucket:
                text = " ".join(str(value).split()).strip()
                if text and text not in items:
                    items.append(text)
        products[issuer] = items
    return {"products": products}


def save_card_products(data: dict, path: Path | None = None) -> None:
    target = _products_path(path)
    atomic_write_text(target, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def list_card_products(path: Path | None = None) -> dict[str, list[str]]:
    return dict(load_card_products(path).get("products") or {})


def append_card_product(
    issuer: str,
    product: str,
    path: Path | None = None,
) -> dict[str, list[str]]:
    cleaned_issuer = normalize_issuer(issuer)
    cleaned_product = " ".join((product or "").split()).strip()
    if not cleaned_product:
        raise ValueError("Card product is required")
    if len(cleaned_product) > 80:
        raise ValueError("Card product is too long")

    target = _products_path(path)
    with FileLock(f"{target}.lock"):
        doc = load_card_products(target)
        products = doc.setdefault("products", {})
        bucket = products.setdefault(cleaned_issuer, [])
        if cleaned_product not in bucket:
            bucket.append(cleaned_product)
        save_card_products(doc, target)
        return list_card_products(target)


def normalize_product(issuer: str | None, value: str | None, *, path: Path | None = None) -> str | None:
    """Trim a product name and enforce the issuer vocabulary when configured."""
    if value is None:
        return None
    clean = " ".join(value.split())
    if len(clean) > 80:
        raise ValueError("Card product is too long")
    if not clean:
        return None

    if not issuer:
        return clean

    try:
        canonical_issuer = normalize_issuer(issuer)
    except ValueError:
        return clean

    allowed = list_card_products(path).get(canonical_issuer) or []
    if not allowed:
        return clean
    if clean not in allowed:
        raise ValueError(
            f"Unsupported {canonical_issuer} product: {clean!r}. "
            f"Choose one of: {', '.join(allowed)}"
        )
    return clean


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
