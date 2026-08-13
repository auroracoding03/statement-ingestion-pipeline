"""Monthly budget envelopes: YAML round-trip, window scaling, and variance."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from src.budget import (
    budget_rows_for_period,
    category_actuals,
    load_budget,
    merge_envelopes,
    save_budget,
    save_envelopes,
    subcategory_actuals,
    window_factor,
)
from src.classify import list_subcategories, load_rules
from src.periods import resolve_period


def _period(**kwargs):
    months = kwargs.pop("months", ["2026-07"])
    return resolve_period(months=months, **kwargs)


def test_load_missing_file_is_empty(tmp_path: Path):
    assert load_budget(tmp_path / "budget.yaml") == {"envelopes": []}


def test_save_load_round_trip_nested_envelopes(tmp_path: Path):
    path = tmp_path / "budget.yaml"
    saved = save_budget(
        {
            "envelopes": [
                {
                    "category": "Food",
                    "amount": 800,
                    "show_on_overview": True,
                    "subcategories": [
                        {"subcategory": "Groceries", "amount": 600, "show_on_overview": True},
                        {"subcategory": "Restaurant", "amount": 200, "show_on_overview": False},
                    ],
                },
                {
                    "category": "Travel",
                    "amount": None,
                    "show_on_overview": False,
                    "subcategories": [],
                },
            ]
        },
        path,
    )
    assert saved["envelopes"][0]["amount"] == 800.0
    assert saved["envelopes"][0]["subcategories"][0]["subcategory"] == "Groceries"
    assert [env["category"] for env in saved["envelopes"]] == ["Food"]

    loaded = load_budget(path)
    assert loaded == saved
    disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert disk["envelopes"][0]["show_on_overview"] is True


def test_merge_lists_every_primary_and_unused_subcategories(tmp_path: Path):
    path = tmp_path / "budget.yaml"
    save_budget(
        {
            "envelopes": [
                {
                    "category": "Food",
                    "amount": 800,
                    "show_on_overview": True,
                    "subcategories": [{"subcategory": "Groceries", "amount": 600, "show_on_overview": True}],
                }
            ]
        },
        path,
    )
    merged = merge_envelopes(
        load_budget(path)["envelopes"],
        categories=["Food", "Travel", "Health"],
        subcategories={"Food": ["Groceries", "Restaurant", "Coffee"], "Travel": ["Lodging"], "Health": []},
    )
    by_cat = {env["category"]: env for env in merged}
    assert list(by_cat) == ["Food", "Travel", "Health"]
    assert by_cat["Food"]["amount"] == 800.0
    assert by_cat["Food"]["available_subcategories"] == ["Restaurant", "Coffee"]
    assert by_cat["Travel"]["amount"] is None
    assert by_cat["Travel"]["available_subcategories"] == ["Lodging"]
    assert by_cat["Health"]["subcategories"] == []


def test_save_envelopes_registers_new_subcategory(tmp_path: Path, monkeypatch):
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        yaml.safe_dump({"categories": ["Food"], "subcategories": {"Food": ["Groceries"]}, "rules": []})
    )
    budget = tmp_path / "budget.yaml"
    monkeypatch.setattr("src.paths.RULES_PATH", rules)

    merged = save_envelopes(
        [
            {
                "category": "Food",
                "amount": 800,
                "show_on_overview": True,
                "subcategories": [{"subcategory": "Dates", "amount": 100, "show_on_overview": True}],
            }
        ],
        budget,
    )
    assert list_subcategories(rules)["Food"] == ["Groceries", "Dates"]
    assert load_rules(rules)["categories"] == ["Food"]
    food = next(env for env in merged if env["category"] == "Food")
    assert [sub["subcategory"] for sub in food["subcategories"]] == ["Dates"]
    assert "Dates" not in food["available_subcategories"]
    assert "Groceries" in food["available_subcategories"]


def test_window_factor_months_and_custom_days():
    month = _period(preset="month", month="2026-07")
    assert window_factor(month) == 1.0

    t12m = _period(preset="t12m", month="2026-07")
    assert window_factor(t12m) == 12.0

    ytd = _period(preset="ytd", month="2026-07")
    assert window_factor(ytd) == 7.0

    custom = _period(preset="custom", since="2026-07-01", until="2026-07-15")
    assert window_factor(custom) == 15 / 30


def test_subcategory_actuals_are_independent_of_parent():
    spend = pd.DataFrame(
        [
            {"category": "Food", "subcategory": "Groceries", "amount": 40.0},
            {"category": "Food", "subcategory": "Restaurant", "amount": 25.0},
            {"category": "Food", "subcategory": None, "amount": 10.0},
            {"category": "Travel", "subcategory": "Lodging", "amount": 200.0},
        ]
    )
    assert category_actuals(spend)["Food"] == 75.0
    assert subcategory_actuals(spend)[("Food", "Groceries")] == 40.0
    assert subcategory_actuals(spend)[("Travel", "Lodging")] == 200.0


def test_budget_rows_only_shown_with_amount_and_variance_sign():
    envelopes = [
        {
            "category": "Food",
            "amount": 60.0,
            "show_on_overview": True,
            "subcategories": [
                {"subcategory": "Groceries", "amount": 30.0, "show_on_overview": True},
                {"subcategory": "Restaurant", "amount": 20.0, "show_on_overview": False},
            ],
        },
        {
            "category": "Travel",
            "amount": 900.0,
            "show_on_overview": False,
            "subcategories": [],
        },
    ]
    spend = pd.DataFrame(
        [
            {"category": "Food", "subcategory": "Groceries", "amount": 40.0},
            {"category": "Food", "subcategory": "Restaurant", "amount": 25.0},
        ]
    )
    rows = budget_rows_for_period(spend, _period(preset="month", month="2026-07"), envelopes=envelopes)
    by_label = {row["label"]: row for row in rows}
    assert set(by_label) == {"Food", "Food / Groceries"}
    assert by_label["Food"]["budget"] == 60.0
    assert by_label["Food"]["actual"] == 65.0
    assert by_label["Food"]["variance"] == 5.0  # actual higher → positive / red
    assert by_label["Food / Groceries"]["budget"] == 30.0
    assert by_label["Food / Groceries"]["actual"] == 40.0
    assert by_label["Food / Groceries"]["variance"] == 10.0


def test_budget_rows_underspend_is_negative_and_t12m_scales():
    envelopes = [
        {
            "category": "Food",
            "amount": 100.0,
            "show_on_overview": True,
            "subcategories": [],
        }
    ]
    spend = pd.DataFrame([{"category": "Food", "subcategory": "Groceries", "amount": 50.0}])
    month_rows = budget_rows_for_period(spend, _period(preset="month", month="2026-07"), envelopes=envelopes)
    assert month_rows[0]["variance"] == -50.0  # budget higher → negative / green

    t12m_rows = budget_rows_for_period(spend, _period(preset="t12m", month="2026-07"), envelopes=envelopes)
    assert t12m_rows[0]["budget"] == 1200.0
    assert t12m_rows[0]["actual"] == 50.0
    assert t12m_rows[0]["variance"] == -1150.0
