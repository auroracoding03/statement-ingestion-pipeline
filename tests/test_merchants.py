"""Canonical merchant identity: alias matching, clustering, and precedence."""

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.classify import classify, load_rules, rewrite_merchant_canonical
from src.merchants import (
    alias_regex_for,
    append_merchant,
    canonicalize,
    cluster_unknowns,
    delete_merchant,
    load_merchants,
    match_canonical,
    merchant_defaults,
    merge_merchants,
    update_merchant,
)
from src.normalize import merchant_identity_key


@pytest.fixture
def merchants_file(tmp_path: Path) -> Path:
    path = tmp_path / "merchants.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "merchants": [
                    {
                        "canonical": "Walmart",
                        "category": "Shopping",
                        "subcategory": "Retail",
                        "aliases": [{"regex": r"(?i)wal[-\s]?mart|\bwlmrt\b|w\s*\*?\s*lmart"}],
                    },
                    {
                        "canonical": "Starbucks",
                        "aliases": [{"exact": "STARBUCKS STORE"}],
                    },
                ]
            }
        )
    )
    return path


def _frame(rows: list[dict]) -> pd.DataFrame:
    base = {
        "txn_id": "t",
        "card": "chase",
        "posted_date": "2026-01-05",
        "amount": 10.0,
        "raw_description": "",
        "normalized_merchant": "",
        "canonical_merchant": None,
        "merchant_source": "none",
        "proposed_canonical": None,
        "source_file": "x.csv",
    }
    return pd.DataFrame([{**base, **r} for r in rows])


@pytest.mark.parametrize(
    "variant",
    ["WAL-MART SUPERCENTER", "WALMART", "WLMRT", "W*LMART", "WAL MART NEIGHBORHOOD"],
)
def test_walmart_variants_collapse_to_one_canonical(merchants_file: Path, variant: str):
    entry = match_canonical(variant, variant, path=merchants_file)
    assert entry is not None, f"{variant} did not match"
    assert entry["canonical"] == "Walmart"


def test_exact_alias_matches_only_exact(merchants_file: Path):
    assert match_canonical("STARBUCKS STORE", path=merchants_file)["canonical"] == "Starbucks"
    assert match_canonical("STARBUCKS RESERVE ROASTERY", path=merchants_file) is None


def test_canonicalize_sets_source_and_preserves_manual(merchants_file: Path):
    frame = _frame(
        [
            {"normalized_merchant": "WLMRT", "raw_description": "WLMRT #221"},
            {"normalized_merchant": "SOME LOCAL SHOP", "raw_description": "SOME LOCAL SHOP"},
            {
                "normalized_merchant": "ODD NAME",
                "canonical_merchant": "Hand Picked",
                "merchant_source": "manual",
            },
        ]
    )
    out = canonicalize(frame, path=merchants_file)

    assert out.iloc[0]["canonical_merchant"] == "Walmart"
    assert out.iloc[0]["merchant_source"] == "alias"
    assert out.iloc[1]["canonical_merchant"] is None
    assert out.iloc[1]["merchant_source"] == "none"
    # A human decision is never overwritten by re-derivation
    assert out.iloc[2]["canonical_merchant"] == "Hand Picked"
    assert out.iloc[2]["merchant_source"] == "manual"


def test_cluster_unknowns_groups_similar_names():
    frame = _frame(
        [
            {"normalized_merchant": "LOCAL COFFEE ROASTERS DOWNTOWN", "amount": 6.0},
            {"normalized_merchant": "LOCAL COFFEE ROASTERS", "amount": 7.0},
            {"normalized_merchant": "THE WEDDING VENUE DEPOSIT", "amount": 500.0},
        ]
    )
    clusters = cluster_unknowns(frame, threshold=80)

    by_rep = {c["representative"]: c for c in clusters}
    coffee = next(c for c in clusters if "COFFEE" in c["representative"])
    assert len(coffee["members"]) == 2
    assert "THE WEDDING VENUE DEPOSIT" in by_rep
    # Highest spend surfaces first for review
    assert clusters[0]["representative"] == "THE WEDDING VENUE DEPOSIT"


def test_cluster_ignores_shared_city_state():
    frame = _frame(
        [
            {"normalized_merchant": "CAVA EAST COBB MARIETTA GA", "amount": 20.0},
            {"normalized_merchant": "EAST COBB AUTO CARE MARIETTA GA", "amount": 80.0},
        ]
    )
    clusters = cluster_unknowns(frame, threshold=80)
    assert len(clusters) == 2
    members = {frozenset(c["members"]) for c in clusters}
    assert frozenset(["CAVA EAST COBB MARIETTA GA"]) in members
    assert frozenset(["EAST COBB AUTO CARE MARIETTA GA"]) in members


def test_cluster_separates_apple_pay_underlying_merchants():
    frame = _frame(
        [
            {"normalized_merchant": "APLPAY ORCA SEATTLE WA", "amount": 3.0},
            {"normalized_merchant": "APLPAY STARBUCKS SEATTLE WA", "amount": 6.0},
            {"normalized_merchant": "APLPAY ORCA TACOMA WA", "amount": 2.5},
        ]
    )
    clusters = cluster_unknowns(frame, threshold=80)
    by_members = {frozenset(c["members"]): c for c in clusters}
    orca = next(members for members in by_members if any("ORCA" in m for m in members))
    assert orca == frozenset(["APLPAY ORCA SEATTLE WA", "APLPAY ORCA TACOMA WA"])
    assert frozenset(["APLPAY STARBUCKS SEATTLE WA"]) in by_members


def test_merchant_identity_key_strips_rail_and_geo():
    assert merchant_identity_key("APLPAY ORCA 00SJFQR SEATTLE WA") == "ORCA 00SJFQR"
    assert merchant_identity_key("CAVA EAST COBB MARIETTA GA") == "CAVA EAST COBB"
    assert merchant_identity_key("APLPAY") == "APLPAY"


def test_cluster_ignores_already_canonical(merchants_file: Path):
    frame = canonicalize(
        _frame(
            [
                {"normalized_merchant": "WALMART", "raw_description": "WALMART"},
                {"normalized_merchant": "MYSTERY VENDOR", "raw_description": "MYSTERY VENDOR"},
            ]
        ),
        path=merchants_file,
    )
    clusters = cluster_unknowns(frame)
    reps = {c["representative"] for c in clusters}
    assert reps == {"MYSTERY VENDOR"}


def test_alias_regex_for_matches_all_members():
    import re

    members = ["LOCAL COFFEE ROASTERS DOWNTOWN", "LOCAL COFFEE ROASTERS"]
    pattern = re.compile(alias_regex_for(members))
    for member in members:
        assert pattern.search(member)


def test_append_and_delete_merchant_roundtrip(merchants_file: Path):
    append_merchant(
        canonical="Local Coffee Roasters",
        members=["LOCAL COFFEE ROASTERS DOWNTOWN"],
        category="Food",
        subcategory="Coffee",
        path=merchants_file,
    )
    entry = match_canonical("LOCAL COFFEE ROASTERS DOWNTOWN", path=merchants_file)
    assert entry["canonical"] == "Local Coffee Roasters"
    assert merchant_defaults(merchants_file)["Local Coffee Roasters"]["category"] == "Food"

    # Extending an existing canonical appends rather than duplicating
    append_merchant(
        canonical="Local Coffee Roasters",
        members=["LCR CAFE"],
        path=merchants_file,
    )
    entries = [
        e for e in load_merchants(merchants_file)["merchants"] if e["canonical"] == "Local Coffee Roasters"
    ]
    assert len(entries) == 1
    assert len(entries[0]["aliases"]) == 2

    assert delete_merchant("Local Coffee Roasters", path=merchants_file) is True
    assert delete_merchant("Local Coffee Roasters", path=merchants_file) is False


def test_canonical_rule_beats_regex_rule(tmp_path: Path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "categories": ["Shopping", "Food"],
                "rules": [
                    {"match": {"merchant_canonical": "Walmart"}, "category": "Shopping", "subcategory": "Retail"},
                    {"match": {"merchant_regex": "(?i)wal"}, "category": "Food", "subcategory": "Wrong"},
                ],
            }
        )
    )
    frame = _frame(
        [{"normalized_merchant": "WLMRT", "canonical_merchant": "Walmart", "merchant_source": "alias"}]
    )
    out = classify(frame, rules_path=rules)
    assert out.iloc[0]["category"] == "Shopping"
    assert out.iloc[0]["classified_by"] == "rule"


def test_merchant_default_applies_when_no_rule_matches(tmp_path: Path, monkeypatch):
    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump({"categories": [], "rules": []}))

    monkeypatch.setattr(
        "src.classify.merchant_defaults",
        lambda: {"Walmart": {"category": "Shopping", "subcategory": "Retail"}},
    )
    frame = _frame([{"normalized_merchant": "WLMRT", "canonical_merchant": "Walmart"}])
    out = classify(frame, rules_path=rules)
    assert out.iloc[0]["category"] == "Shopping"
    assert out.iloc[0]["classified_by"] == "merchant"


def test_update_merchant_replaces_aliases_and_renames(merchants_file: Path):
    updated = update_merchant(
        "Walmart",
        canonical="Walmart Inc",
        aliases=[{"exact": "WALMART"}, {"regex": r"(?i)wmt"}],
        category="Shopping",
        subcategory="Retail",
        path=merchants_file,
    )
    assert updated["canonical"] == "Walmart Inc"
    assert updated["aliases"] == [{"exact": "WALMART"}, {"regex": r"(?i)wmt"}]
    assert updated["category"] == "Shopping"
    assert updated["subcategory"] == "Retail"
    assert match_canonical("WALMART", path=merchants_file)["canonical"] == "Walmart Inc"
    with pytest.raises(KeyError):
        update_merchant("Walmart", aliases=[{"exact": "X"}], path=merchants_file)


def test_update_merchant_rejects_duplicate_canonical(merchants_file: Path):
    with pytest.raises(ValueError, match="already exists"):
        update_merchant("Starbucks", canonical="Walmart", path=merchants_file)


def test_update_merchant_requires_a_valid_alias(merchants_file: Path):
    with pytest.raises(ValueError, match="At least one alias"):
        update_merchant("Walmart", aliases=[], path=merchants_file)
    with pytest.raises(ValueError, match="Invalid alias regex"):
        update_merchant("Walmart", aliases=[{"regex": "["}], path=merchants_file)


def test_merge_merchants_concatenates_aliases_drops_source_and_rewrites_rules(
    merchants_file: Path, tmp_path: Path
):
    append_merchant(
        canonical="GPC",
        aliases=[{"regex": r"(?i)gpc paymentus"}],
        category="Utilities",
        path=merchants_file,
    )
    merged = merge_merchants("GPC", "Walmart", path=merchants_file)
    names = [entry["canonical"] for entry in load_merchants(merchants_file)["merchants"]]
    assert "GPC" not in names
    assert merged["canonical"] == "Walmart"
    assert merged["category"] == "Shopping"
    regexes = [alias.get("regex") for alias in merged["aliases"]]
    assert r"(?i)gpc paymentus" in regexes
    assert any(alias and "wal" in alias.lower() for alias in regexes)

    rules = tmp_path / "rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "categories": ["Utilities"],
                "rules": [{"match": {"merchant_canonical": "GPC"}, "category": "Utilities"}],
            }
        )
    )
    assert rewrite_merchant_canonical("GPC", "Walmart", rules_path=rules) == 1
    assert load_rules(rules)["rules"][0]["match"]["merchant_canonical"] == "Walmart"


def test_merge_orphan_source_leaves_target_yaml_unchanged(merchants_file: Path):
    before = load_merchants(merchants_file)
    merged = merge_merchants("GPC", "Walmart", path=merchants_file)
    assert merged["canonical"] == "Walmart"
    assert load_merchants(merchants_file) == before


def test_merge_skips_duplicate_aliases(merchants_file: Path):
    walmart = next(
        entry for entry in load_merchants(merchants_file)["merchants"] if entry["canonical"] == "Walmart"
    )
    append_merchant(canonical="GPC", aliases=list(walmart["aliases"]), path=merchants_file)
    merged = merge_merchants("GPC", "Walmart", path=merchants_file)
    assert merged["aliases"] == walmart["aliases"]


def test_merge_refuses_unknown_target_and_self(merchants_file: Path):
    with pytest.raises(KeyError):
        merge_merchants("GPC", "Nope", path=merchants_file)
    with pytest.raises(ValueError, match="different"):
        merge_merchants("Walmart", "walmart", path=merchants_file)
    with pytest.raises(ValueError, match="required"):
        merge_merchants("  ", "Walmart", path=merchants_file)
