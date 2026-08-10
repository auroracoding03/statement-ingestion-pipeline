"""Managed subcategory vocabulary helpers."""

from pathlib import Path

import yaml

from src.classify import (
    append_category,
    append_rule,
    append_subcategory,
    list_subcategories,
    load_rules,
    rule_pattern_from_merchant,
    update_rule,
)


def test_list_and_append_subcategories(tmp_path: Path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "categories": ["Food", "Transport"],
                "subcategories": {"Food": ["Groceries"], "Transport": []},
                "rules": [],
            }
        )
    )

    listed = list_subcategories(rules)
    assert listed["Food"] == ["Groceries"]
    assert listed["Transport"] == []

    updated = append_subcategory("Food", "Coffee", rules_path=rules)
    assert updated["Food"] == ["Groceries", "Coffee"]

    # Idempotent
    again = append_subcategory("Food", "Coffee", rules_path=rules)
    assert again["Food"] == ["Groceries", "Coffee"]


def test_append_category_seeds_empty_subcategory_bucket(tmp_path: Path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump({"categories": ["Food"], "subcategories": {}, "rules": []}))
    cats = append_category("Travel", rules_path=rules)
    assert "Travel" in cats
    assert list_subcategories(rules)["Travel"] == []


def test_append_rule_registers_subcategory(tmp_path: Path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump({"categories": ["Food"], "subcategories": {}, "rules": []}))
    append_rule(
        merchant_canonical="Starbucks",
        category="Food",
        subcategory="Coffee",
        rules_path=rules,
    )
    assert "Coffee" in list_subcategories(rules)["Food"]


def test_update_rule_sets_subcategory_and_registers_vocab(tmp_path: Path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "categories": ["Utilities"],
                "subcategories": {"Utilities": ["Electric"]},
                "rules": [
                    {
                        "match": {"merchant_regex": "(?i)SPI\\s+GA\\s+NATGAS"},
                        "category": "Utilities",
                        "subcategory": "",
                    }
                ],
            }
        )
    )

    updated = update_rule(
        0,
        category="Utilities",
        subcategory="NaturalGas",
        rules_path=rules,
    )
    assert updated is not None
    assert updated["category"] == "Utilities"
    assert updated["subcategory"] == "NaturalGas"
    assert updated["match"]["merchant_regex"] == "(?i)SPI\\s+GA\\s+NATGAS"
    assert list_subcategories(rules)["Utilities"] == ["Electric", "NaturalGas"]

    cleared = update_rule(0, category="Utilities", subcategory="", rules_path=rules)
    assert cleared is not None
    assert cleared["subcategory"] == ""

    assert update_rule(99, category="Utilities", rules_path=rules) is None


def test_load_rules_reads_utf8_en_dash_on_windows(tmp_path: Path) -> None:
    """Review-created rules may embed Unicode dashes; reload must not use locale encoding."""
    rules = tmp_path / "rules.yaml"
    content = (
        "categories:\n"
        "- Transfers\n"
        "rules:\n"
        "- match:\n"
        "    merchant_regex: (?i)Mobile\\s+Payment\\s*[\\-–—]\\s*Thank\\s+You\n"
        "  category: Transfers\n"
        "  subcategory: ''\n"
    )
    rules.write_bytes(content.encode("utf-8"))

    doc = load_rules(rules)
    assert doc["categories"] == ["Transfers"]
    pattern = doc["rules"][0]["match"]["merchant_regex"]
    assert "Mobile" in pattern


def test_rule_pattern_from_merchant_folds_unicode_dashes() -> None:
    pattern = rule_pattern_from_merchant("Mobile Payment – Thank You")
    assert "–" not in pattern
    assert "—" not in pattern
    assert pattern.startswith("(?i)")
    assert "Mobile" in pattern
    assert "Thank" in pattern


def test_append_rule_with_unicode_merchant_reloads_cleanly(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump({"categories": ["Transfers"], "rules": []}), encoding="utf-8")
    append_rule(
        merchant_regex=rule_pattern_from_merchant("Mobile Payment – Thank You"),
        category="Transfers",
        rules_path=rules,
    )
    doc = load_rules(rules)
    assert doc["rules"][0]["category"] == "Transfers"
    assert "–" not in doc["rules"][0]["match"]["merchant_regex"]
