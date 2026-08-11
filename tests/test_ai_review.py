"""Focused coverage for durable, approval-only local AI review."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src import ai_review, paths
from src.atomic import atomic_write_parquet
from src.normalize import make_txn_id


def _configure(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    config = tmp_path / "config"
    snapshots = data / "ai_snapshots"
    data.mkdir()
    config.mkdir()
    snapshots.mkdir()
    rules = config / "rules.yaml"
    merchants = config / "merchants.yaml"
    rules.write_text(yaml.safe_dump({"categories": ["Food", "Shopping"], "rules": []}))
    merchants.write_text("merchants: []\n")
    monkeypatch.setattr(paths, "RULES_PATH", rules)
    monkeypatch.setattr(paths, "MERCHANTS_PATH", merchants)
    monkeypatch.setattr(paths, "AI_PROPOSALS_PARQUET", data / "ai_proposals.parquet")
    monkeypatch.setattr(paths, "AI_APPLICATIONS_PATH", data / "ai_applications.json")
    monkeypatch.setattr(paths, "AI_SNAPSHOTS", snapshots)
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    return data, rules, merchants


def _ledger() -> pd.DataFrame:
    merchant = "WAL-MART #1234 ATLANTA GA"
    txn_id = make_txn_id("chase", "2026-01-06", 84.19, merchant)
    return pd.DataFrame(
        [
            {
                "txn_id": txn_id,
                "card": "chase",
                "posted_date": "2026-01-06",
                "amount": 84.19,
                "raw_description": merchant,
                "normalized_merchant": "WAL-MART ATLANTA GA",
                "canonical_merchant": None,
                "merchant_source": "none",
                "proposed_canonical": None,
                "category": None,
                "subcategory": None,
                "classified_by": None,
                "proposed_category": None,
                "proposed_subcategory": None,
            }
        ]
    )


def test_analysis_is_grouped_and_does_not_mutate_ledger(tmp_path: Path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    ledger = _ledger()
    original = ledger.copy(deep=True)
    monkeypatch.setattr(ai_review, "ollama_available", lambda host: True)

    def fake_ask(kind, profiles, categories, cfg):
        if kind == "merchant":
            return [{"key": p["key"], "canonical": "Walmart", "confidence": "high", "reason": "Known brand", "ambiguous": False} for p in profiles]
        return [{"key": p["key"], "category": "Shopping", "subcategory": "Retail", "confidence": "medium", "reason": "Retailer", "ambiguous": True} for p in profiles]

    monkeypatch.setattr(ai_review, "_ask_batch", fake_ask)
    outcome = ai_review.run_analysis(ledger)

    assert outcome["created"] == 2
    pd.testing.assert_frame_equal(ledger, original)
    proposals = ai_review.list_proposals()
    assert {item["kind"] for item in proposals["items"]} == {"merchant", "category"}
    merchant = next(item for item in proposals["items"] if item["kind"] == "merchant")
    assert merchant["recommendation"]["canonical"] == "Walmart"


def test_approved_merchant_is_snapshotted_and_can_be_rolled_back(tmp_path: Path, monkeypatch):
    data, _rules, merchants = _configure(tmp_path, monkeypatch)
    ledger = _ledger()
    ledger_path = data / "ledger.parquet"
    atomic_write_parquet(ledger, ledger_path)
    proposal = {
        "proposal_id": "merchant-proposal",
        "input_fingerprint": "fingerprint",
        "kind": "merchant",
        "status": "pending",
        "members_json": json.dumps(["WAL-MART ATLANTA GA"]),
        "txn_ids_json": json.dumps(ledger["txn_id"].tolist()),
        "recommendation_json": json.dumps({"canonical": "Walmart"}),
        "evidence_json": json.dumps({"txn_count": 1}),
        "confidence": "high",
        "model": "qwen3.5:9b",
        "prompt_version": ai_review.PROMPT_VERSION,
        "batch_id": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "error": None,
    }
    ai_review._write_proposals(pd.DataFrame([proposal]))

    result = ai_review.apply_decisions(
        ledger,
        lambda frame: atomic_write_parquet(frame, ledger_path),
        [{"proposal_id": "merchant-proposal", "action": "accept"}],
        ledger_path=ledger_path,
    )
    assert result["applied"] == ["merchant-proposal"]
    assert pd.read_parquet(ledger_path).iloc[0]["canonical_merchant"] == "Walmart"
    assert yaml.safe_load(merchants.read_text())["merchants"][0]["canonical"] == "Walmart"

    rollback = ai_review.rollback_latest(ledger_path)
    assert rollback["rolled_back"] == result["batch_id"]
    assert pd.isna(pd.read_parquet(ledger_path).iloc[0]["canonical_merchant"])
    assert ai_review.list_proposals()["items"][0]["status"] == "pending"


def test_failed_approval_restores_config_and_ledger(tmp_path: Path, monkeypatch):
    data, _rules, merchants = _configure(tmp_path, monkeypatch)
    ledger = _ledger()
    ledger_path = data / "ledger.parquet"
    atomic_write_parquet(ledger, ledger_path)
    ai_review._write_proposals(
        pd.DataFrame(
            [
                {
                    "proposal_id": "merchant-failure",
                    "input_fingerprint": "failure",
                    "kind": "merchant",
                    "status": "pending",
                    "members_json": json.dumps(["WAL-MART ATLANTA GA"]),
                    "txn_ids_json": json.dumps(ledger["txn_id"].tolist()),
                    "recommendation_json": json.dumps({"canonical": "Walmart"}),
                    "evidence_json": "{}",
                    "confidence": "high",
                    "model": "qwen3.5:9b",
                    "prompt_version": ai_review.PROMPT_VERSION,
                    "batch_id": None,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "error": None,
                }
            ]
        )
    )

    with pytest.raises(RuntimeError, match="write failed"):
        ai_review.apply_decisions(
            ledger,
            lambda _frame: (_ for _ in ()).throw(RuntimeError("write failed")),
            [{"proposal_id": "merchant-failure", "action": "accept"}],
            ledger_path=ledger_path,
        )

    assert pd.isna(pd.read_parquet(ledger_path).iloc[0]["canonical_merchant"])
    assert yaml.safe_load(merchants.read_text()) == {"merchants": []}
    assert ai_review.list_proposals()["items"][0]["status"] == "pending"
