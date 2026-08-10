"""Managed subcategory vocabulary helpers."""

from pathlib import Path

import yaml

from src.classify import append_category, append_rule, append_subcategory, list_subcategories


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
