"""Card-product vocabulary helpers for upload selection."""

from pathlib import Path

import pytest
import yaml

from src.upload_context import (
    append_card_product,
    is_generic_card_product,
    list_card_products,
    normalize_cardholder,
    normalize_product,
    remove_card_product,
    resolve_card_product_for_issuer,
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


def test_remove_card_product_drops_unused_name_and_keeps_empty_bucket(tmp_path: Path):
    products = tmp_path / "card_products.yaml"
    _write_products(
        products,
        {
            "American Express": ["Platinum", "Gold"],
            "Chase": ["Sapphire Preferred"],
            "Bank of America": [],
            "Capital One": [],
            "Wells Fargo": [],
            "Generic": [],
        },
    )

    assert remove_card_product("American Express", "Gold", path=products) is True
    listed = list_card_products(products)
    assert listed["American Express"] == ["Platinum"]
    assert "Gold" not in listed["American Express"]

    assert remove_card_product("chase", "Sapphire Preferred", path=products) is True
    listed = list_card_products(products)
    assert listed["Chase"] == []

    assert remove_card_product("American Express", "Gold", path=products) is False
    assert remove_card_product("American Express", "Mystery Card", path=products) is False
    assert list_card_products(products)["American Express"] == ["Platinum"]


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


def test_resolve_card_product_for_issuer_flags_generic_and_unknown(tmp_path: Path):
    products = tmp_path / "card_products.yaml"
    _write_products(
        products,
        {
            "American Express": [],
            "Chase": [],
            "Bank of America": [],
            "Capital One": [],
            "Wells Fargo": ["Autograph Visa Signature"],
            "Generic": [],
        },
    )

    assert is_generic_card_product("Credit")
    product, needs = resolve_card_product_for_issuer("Wells Fargo", "Credit", path=products)
    assert product is None
    assert needs is True

    product, needs = resolve_card_product_for_issuer("Wells Fargo", "Autograph Visa Signature", path=products)
    assert product == "Autograph Visa Signature"
    assert needs is False


def test_normalize_cardholder_rejects_blank_and_unassigned():
    assert normalize_cardholder("  Alex Example  ") == "Alex Example"
    with pytest.raises(ValueError, match="required"):
        normalize_cardholder("  ")
    with pytest.raises(ValueError, match="Unassigned"):
        normalize_cardholder("Unassigned")
