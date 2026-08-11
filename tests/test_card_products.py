"""Card-product vocabulary helpers for upload selection."""

from pathlib import Path

import pytest
import yaml

from src.upload_context import (
    append_card_product,
    list_card_products,
    normalize_product,
)


def _write_products(path: Path, products: dict[str, list[str]]) -> Path:
    path.write_text(yaml.safe_dump({"products": products}), encoding="utf-8")
    return path


def test_list_and_append_card_products(tmp_path: Path):
    products = tmp_path / "card_products.yaml"
    _write_products(
        products,
        {
            "American Express": ["Platinum"],
            "Chase": [],
            "Bank of America": [],
            "Capital One": [],
            "Wells Fargo": [],
            "Generic": [],
        },
    )

    listed = list_card_products(products)
    assert listed["American Express"] == ["Platinum"]

    updated = append_card_product("American Express", "Delta Gold", path=products)
    assert updated["American Express"] == ["Platinum", "Delta Gold"]

    # Idempotent
    again = append_card_product("amex", "Delta Gold", path=products)
    assert again["American Express"] == ["Platinum", "Delta Gold"]


def test_normalize_product_enforces_vocab_when_configured(tmp_path: Path):
    products = tmp_path / "card_products.yaml"
    _write_products(
        products,
        {
            "American Express": ["Platinum", "Delta Gold"],
            "Chase": [],
            "Bank of America": [],
            "Capital One": [],
            "Wells Fargo": [],
            "Generic": [],
        },
    )

    assert normalize_product("American Express", "Platinum", path=products) == "Platinum"
    with pytest.raises(ValueError, match="Unsupported American Express product"):
        normalize_product("American Express", "Mystery Card", path=products)

    # Empty vocab keeps free-form behavior.
    assert normalize_product("Chase", "Sapphire Preferred", path=products) == "Sapphire Preferred"
