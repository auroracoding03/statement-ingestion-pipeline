"""Category delete helpers: impact counts, vocab rewrite, Uncategorized guard."""

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.classify import drop_vocab, load_rules, rewrite_rule_vocab
from src.taxonomy import category_impact, delete_category
import src.paths as paths


def _write_rules(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "categories": ["Food", "Shopping", "Uncategorized"],
                "subcategories": {"Food": ["Groceries", "Coffee"], "Shopping": ["Retail"]},
                "rules": [
                    {
                        "match": {"merchant_canonical": "Kroger"},
                        "category": "Food",
                        "subcategory": "Groceries",
                    },
                    {
                        "match": {"merchant_regex": "(?i)target"},
                        "category": "Shopping",
                        "subcategory": "Retail",
                    },
                ],
            }
        )
    )


@pytest.fixture
def taxonomy_config(tmp_path: Path, monkeypatch) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    rules = config / "rules.yaml"
    _write_rules(rules)
    merchants = config / "merchants.yaml"
    merchants.write_text(
        yaml.safe_dump(
            {
                "merchants": [
                    {
                        "canonical": "Kroger",
                        "category": "Food",
                        "subcategory": "Groceries",
                        "aliases": [{"exact": "KROGER"}],
                    }
                ]
            }
        )
    )
    expected = config / "expected_recurring.yaml"
    expected.write_text(
        yaml.safe_dump(
            {
                "bills": [
                    {"name": "Groceries", "category": "Food", "subcategory": "Groceries"},
                    {"name": "Internet", "category": "Utilities", "subcategory": "Internet"},
                ]
            }
        )
    )
    monkeypatch.setattr(paths, "RULES_PATH", rules)
    monkeypatch.setattr(paths, "MERCHANTS_PATH", merchants)
    monkeypatch.setattr(paths, "EXPECTED_RECURRING_PATH", expected)
    monkeypatch.setattr(paths, "BUDGET_PATH", config / "budget.yaml")
    return config


def test_category_impact_counts(taxonomy_config: Path):
    ledger = pd.DataFrame(
        [
            {"category": "Food", "subcategory": "Groceries"},
            {"category": "Food", "subcategory": "Coffee"},
            {"category": "Shopping", "subcategory": "Retail"},
        ]
    )
    food = category_impact(ledger, "Food")
    assert food["txn_count"] == 2
    assert food["rule_count"] == 1
    assert food["merchant_count"] == 1
    assert food["bill_count"] == 1

    groceries = category_impact(ledger, "Food", "Groceries")
    assert groceries["txn_count"] == 1
    assert groceries["rule_count"] == 1
    assert groceries["merchant_count"] == 1
    assert groceries["bill_count"] == 1

    coffee = category_impact(ledger, "Food", "Coffee")
    assert coffee["txn_count"] == 1
    assert coffee["rule_count"] == 0
    assert coffee["merchant_count"] == 0


def test_drop_vocab_subcategory_leaves_parent(taxonomy_config: Path):
    rules = taxonomy_config / "rules.yaml"
    assert drop_vocab("Food", "Groceries", rules_path=rules) is True
    doc = load_rules(rules)
    assert "Food" in doc["categories"]
    assert doc["subcategories"]["Food"] == ["Coffee"]
    assert drop_vocab("Food", rules_path=rules) is True
    doc = load_rules(rules)
    assert "Food" not in doc["categories"]
    assert "Food" not in doc["subcategories"]


def test_rewrite_rule_vocab_unassign_and_reassign(taxonomy_config: Path):
    rules = taxonomy_config / "rules.yaml"
    assert rewrite_rule_vocab("Food", "Groceries", action="unassign", rules_path=rules) == 1
    remaining = load_rules(rules)["rules"]
    assert remaining == [
        {"match": {"merchant_regex": "(?i)target"}, "category": "Shopping", "subcategory": "Retail"}
    ]

    _write_rules(rules)
    assert (
        rewrite_rule_vocab(
            "Food",
            "Groceries",
            action="reassign",
            reassign_category="Shopping",
            reassign_subcategory="Retail",
            rules_path=rules,
        )
        == 1
    )
    rewritten = load_rules(rules)["rules"]
    assert rewritten[0]["category"] == "Shopping"
    assert rewritten[0]["subcategory"] == "Retail"
    assert rewritten[0]["match"]["merchant_canonical"] == "Kroger"


def test_delete_uncategorized_is_refused(taxonomy_config: Path):
    with pytest.raises(ValueError, match="Uncategorized cannot be deleted"):
        delete_category(pd.DataFrame(), "Uncategorized", action="unassign")
    with pytest.raises(KeyError):
        delete_category(pd.DataFrame(), "NoSuch", action="unassign")
