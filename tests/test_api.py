"""API surface tests, with all writes redirected into a temp workspace."""

from pathlib import Path

import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.app as api_app
import src.paths as paths_mod
import src.pipeline as pipeline_mod
import src.store as store_mod
from src.normalize import make_txn_id


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    """Point every module-level path constant at an isolated directory."""
    data = tmp_path / "data"
    data.mkdir()
    config = tmp_path / "config"
    config.mkdir()

    rules_path = config / "rules.yaml"
    rules_path.write_text(
        yaml.safe_dump(
            {
                "categories": ["Food", "Shopping", "Utilities"],
                "rules": [
                    {
                        "match": {"merchant_regex": "(?i)netflix"},
                        "category": "Subscriptions",
                        "subcategory": "Streaming",
                    }
                ],
            }
        )
    )
    merchants_path = config / "merchants.yaml"
    merchants_path.write_text(
        yaml.safe_dump(
            {
                "merchants": [
                    {
                        "canonical": "Walmart",
                        "category": "Shopping",
                        "aliases": [{"regex": r"(?i)wal[-\s]?mart|\bwlmrt\b"}],
                    }
                ]
            }
        )
    )

    ledger_path = data / "ledger.parquet"
    # Ids must be the real hashes, otherwise load_ledger() correctly treats the
    # ledger as legacy and migrates it out from under the test.
    walmart_id = make_txn_id("chase", "2026-01-06", 84.19, "WAL-MART #1234 ATLANTA GA")
    coffee_id = make_txn_id("chase", "2026-01-10", 6.75, "LOCAL COFFEE ROASTERS DOWNTOWN")

    ledger = pd.DataFrame(
        [
            {
                "txn_id": walmart_id,
                "card": "chase",
                "posted_date": "2026-01-06",
                "amount": 84.19,
                "raw_description": "WAL-MART #1234 ATLANTA GA",
                "normalized_merchant": "WAL-MART ATLANTA GA",
                "canonical_merchant": None,
                "merchant_source": "none",
                "proposed_canonical": None,
                "source_file": "chase/2026-01.csv",
                "category": None,
                "subcategory": None,
                "classified_by": None,
                "proposed_category": None,
                "proposed_subcategory": None,
            },
            {
                "txn_id": coffee_id,
                "card": "chase",
                "posted_date": "2026-01-10",
                "amount": 6.75,
                "raw_description": "LOCAL COFFEE ROASTERS DOWNTOWN",
                "normalized_merchant": "LOCAL COFFEE ROASTERS DOWNTOWN",
                "canonical_merchant": None,
                "merchant_source": "none",
                "proposed_canonical": None,
                "source_file": "chase/2026-01.csv",
                "category": None,
                "subcategory": None,
                "classified_by": None,
                "proposed_category": None,
                "proposed_subcategory": None,
            },
        ]
    )
    ledger.to_parquet(ledger_path, index=False)

    # classify/merchants resolve these lazily off the paths module
    monkeypatch.setattr(paths_mod, "RULES_PATH", rules_path)
    monkeypatch.setattr(paths_mod, "MERCHANTS_PATH", merchants_path)
    monkeypatch.setattr(paths_mod, "MANUAL_OBLIGATIONS_PATH", config / "manual_obligations.yaml")
    (config / "manual_obligations.yaml").write_text("version: 1\nobligations: []\n")
    monkeypatch.setattr(paths_mod, "OBLIGATION_OCCURRENCES_PATH", data / "manual_obligation_occurrences.json")
    monkeypatch.setattr(paths_mod, "OBLIGATIONS_LOCK", data / "manual_obligations.lock")
    monkeypatch.setattr(paths_mod, "DATA", data)

    # These were bound into module namespaces at import time
    for module in (pipeline_mod, store_mod, api_app):
        monkeypatch.setattr(module, "LEDGER_PARQUET", ledger_path, raising=False)
    monkeypatch.setattr(pipeline_mod, "LEDGER_LOCK", data / "ledger.lock", raising=False)
    monkeypatch.setattr(pipeline_mod, "PROPOSALS_PARQUET", data / "proposals.parquet", raising=False)
    monkeypatch.setattr(api_app, "INBOX", tmp_path / "inbox", raising=False)

    original_write = store_mod.write_ledger
    monkeypatch.setattr(
        store_mod, "write_ledger", lambda df, path=ledger_path: original_write(df, path)
    )
    monkeypatch.setattr(
        pipeline_mod, "write_ledger", lambda df, path=ledger_path: original_write(df, path)
    )
    # ensure_dirs would recreate the real project folders
    monkeypatch.setattr(pipeline_mod, "ensure_dirs", lambda: None)
    monkeypatch.setattr(store_mod, "ensure_dirs", lambda: None)

    return {"root": tmp_path, "walmart_id": walmart_id, "coffee_id": coffee_id}


@pytest.fixture
def client(workspace: dict) -> TestClient:  # noqa: ARG001 — fixture ordering matters
    return TestClient(api_app.app)


def test_status_reports_counts(client: TestClient):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ledger_exists"] is True
    assert body["counts"]["total"] == 2


def test_transactions_search_and_filter(client: TestClient):
    r = client.get("/api/transactions?q=coffee")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = client.get("/api/transactions?card=nope")
    assert r.json()["total"] == 0

    r = client.get("/api/transactions?unclassified=true")
    assert r.json()["total"] == 2


def test_review_queue_and_decision_creates_rule(client: TestClient, workspace: dict):
    queue = client.get("/api/review/queue").json()
    assert queue["total"] == 2
    assert "Food" in queue["categories"]

    r = client.post(
        f"/api/review/{workspace['coffee_id']}",
        json={"category": "Food", "subcategory": "Coffee", "create_rule": True},
    )
    assert r.status_code == 200
    assert r.json()["rule"] is not None

    # Decision is persisted and drops out of the queue
    assert client.get("/api/review/queue").json()["total"] == 1
    rules = client.get("/api/rules").json()["rules"]
    assert any(r["category"] == "Food" for r in rules)


def test_review_unknown_transaction_is_404(client: TestClient):
    r = client.post("/api/review/does-not-exist", json={"category": "Food"})
    assert r.status_code == 404


def test_unknown_clusters_then_confirm_merchant(client: TestClient):
    clusters = client.get("/api/merchants/unknown").json()
    reps = {c["representative"] for c in clusters["items"]}
    assert "LOCAL COFFEE ROASTERS DOWNTOWN" in reps

    r = client.post(
        "/api/merchants",
        json={
            "canonical": "Local Coffee Roasters",
            "members": ["LOCAL COFFEE ROASTERS DOWNTOWN"],
            "category": "Food",
            "subcategory": "Coffee",
            "restamp": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["stamped"] == 1

    merchants = client.get("/api/merchants").json()
    names = {m["canonical"] for m in merchants["items"]}
    assert "Local Coffee Roasters" in names

    # The confirmed merchant no longer appears as unresolved
    after = client.get("/api/merchants/unknown").json()
    assert "LOCAL COFFEE ROASTERS DOWNTOWN" not in {c["representative"] for c in after["items"]}


def test_walmart_variant_resolves_via_alias(client: TestClient):
    client.post("/api/merchants/recanonicalize")
    items = client.get("/api/transactions?q=wal").json()["items"]
    assert items[0]["canonical_merchant"] == "Walmart"
    assert items[0]["merchant_source"] == "alias"


def test_merchant_requires_aliases_or_members(client: TestClient):
    r = client.post("/api/merchants", json={"canonical": "Nothing"})
    assert r.status_code == 400


def test_rules_add_and_delete(client: TestClient):
    before = len(client.get("/api/rules").json()["rules"])
    r = client.post(
        "/api/rules",
        json={"merchant_canonical": "Walmart", "category": "Shopping", "subcategory": "Retail"},
    )
    assert r.status_code == 200
    assert len(client.get("/api/rules").json()["rules"]) == before + 1

    assert client.delete("/api/rules/0").status_code == 200
    assert len(client.get("/api/rules").json()["rules"]) == before
    assert client.delete("/api/rules/999").status_code == 404


def test_rule_requires_a_matcher(client: TestClient):
    r = client.post("/api/rules", json={"category": "Food"})
    assert r.status_code == 400


def test_classify_job_runs_to_completion(client: TestClient):
    started = client.post("/api/classify", json={"with_ai": False})
    assert started.status_code == 200
    job_id = started.json()["job_id"]

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done"
    assert job["result"]["total"] == 2


def test_unknown_job_is_404(client: TestClient):
    assert client.get("/api/jobs/nope").status_code == 404


def test_obligation_crud_and_unknown_id(client: TestClient):
    created = client.post(
        "/api/obligations",
        json={
            "name": "Mortgage 1",
            "category": "Housing",
            "subcategory": "Mortgage",
            "expected_amount_cents": 210000,
            "due_day": 1,
        },
    )
    assert created.status_code == 200
    oid = created.json()["obligation"]["id"]

    listed = client.get("/api/obligations").json()
    assert listed["total"] == 1
    assert listed["items"][0]["name"] == "Mortgage 1"

    updated = client.put(
        f"/api/obligations/{oid}",
        json={
            "name": "Mortgage 1 Primary",
            "category": "Housing",
            "subcategory": "Mortgage",
            "expected_amount_cents": 215000,
            "due_day": 1,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["obligation"]["name"] == "Mortgage 1 Primary"
    assert updated.json()["obligation"]["id"] == oid

    assert client.delete("/api/obligations/does-not-exist").status_code == 404
    assert client.put(
        "/api/obligations/does-not-exist",
        json={
            "name": "x",
            "category": "Housing",
            "expected_amount_cents": 100,
            "due_day": 1,
        },
    ).status_code == 404

    deactivated = client.delete(f"/api/obligations/{oid}")
    assert deactivated.status_code == 200
    assert deactivated.json()["obligation"]["active"] is False
    assert client.get("/api/obligations").json()["total"] == 0
    assert client.get("/api/obligations?active_only=false").json()["total"] == 1


def test_obligation_month_confirm_reset_and_category_totals(client: TestClient, workspace: dict):
    ledger_path = workspace["root"] / "data" / "ledger.parquet"
    before = ledger_path.read_bytes()

    oid = client.post(
        "/api/obligations",
        json={
            "name": "Mortgage 1",
            "category": "Housing",
            "subcategory": "Mortgage",
            "expected_amount_cents": 210000,
            "due_day": 1,
        },
    ).json()["obligation"]["id"]

    month = client.get("/api/obligation-months/2026-08").json()
    assert month["items"][0]["status"] in ("expected", "overdue")
    assert month["expected_total_cents"] == 210000
    assert month["paid_total_cents"] == 0

    # Expected / overdue must not inflate category totals
    cats_before_pay = { (r["month"], r["category"]): r["total"] for r in client.get("/api/categories/monthly").json() }
    assert ("2026-08", "Housing") not in cats_before_pay

    paid = client.put(
        f"/api/obligation-months/2026-08/{oid}",
        json={"status": "paid", "actual_amount_cents": 210000, "paid_date": "2026-08-01"},
    )
    assert paid.status_code == 200

    cats = client.get("/api/categories/monthly").json()
    housing_aug = next(r for r in cats if r["month"] == "2026-08" and r["category"] == "Housing")
    assert housing_aug["total"] == 2100.0
    assert housing_aug["txn_count"] == 1

    # Skipped replaces paid and must drop out of category totals
    skipped = client.put(
        f"/api/obligation-months/2026-08/{oid}",
        json={"status": "skipped"},
    )
    assert skipped.status_code == 200
    cats_skipped = {
        (r["month"], r["category"]): r["total"] for r in client.get("/api/categories/monthly").json()
    }
    assert ("2026-08", "Housing") not in cats_skipped

    # Re-pay then reset
    client.put(
        f"/api/obligation-months/2026-08/{oid}",
        json={"status": "paid", "actual_amount_cents": 210000, "paid_date": "2026-08-01"},
    )
    reset = client.delete(f"/api/obligation-months/2026-08/{oid}")
    assert reset.status_code == 200
    assert reset.json()["cleared"] is True
    cats_reset = {
        (r["month"], r["category"]): r["total"] for r in client.get("/api/categories/monthly").json()
    }
    assert ("2026-08", "Housing") not in cats_reset

    assert ledger_path.read_bytes() == before


def test_obligation_confirm_unknown_id_is_404(client: TestClient):
    r = client.put(
        "/api/obligation-months/2026-08/nope",
        json={"status": "paid", "actual_amount_cents": 100, "paid_date": "2026-08-01"},
    )
    assert r.status_code == 404
