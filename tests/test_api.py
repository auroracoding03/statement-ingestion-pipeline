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
from src.upload_context import sidecar_path


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

    # These were bound into module namespaces at import time
    for module in (pipeline_mod, store_mod, api_app):
        monkeypatch.setattr(module, "LEDGER_PARQUET", ledger_path, raising=False)
    monkeypatch.setattr(pipeline_mod, "LEDGER_LOCK", data / "ledger.lock", raising=False)
    monkeypatch.setattr(pipeline_mod, "PROPOSALS_PARQUET", data / "proposals.parquet", raising=False)
    monkeypatch.setattr(api_app, "INBOX", tmp_path / "inbox", raising=False)
    monkeypatch.setattr(api_app, "ensure_dirs", lambda: None)

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


def test_updates_are_exposed_without_network_access(client: TestClient):
    r = client.get("/api/updates")

    assert r.status_code == 200
    assert r.json()["supported"] is False


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


def test_upload_never_overwrites_an_existing_source_document(client: TestClient, workspace: dict):
    payload = b"Date,Description,Amount\n2026-01-01,Coffee,10.00\n"
    first = client.post(
        "/api/upload?card=generic",
        files=[("files", ("statement.csv", payload, "text/csv"))],
    )
    second = client.post(
        "/api/upload?card=generic",
        files=[("files", ("statement.csv", payload, "text/csv"))],
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_name = first.json()["written"][0]
    second_name = second.json()["written"][0]
    assert first_name != second_name
    assert (workspace["root"] / "inbox" / first_name).exists()
    assert (workspace["root"] / "inbox" / second_name).exists()


def test_amex_upload_persists_selected_parser_context(client: TestClient, workspace: dict):
    payload = b"Date,Description,Card Member,Account #,Amount\n2026-01-01,Coffee,ALEX EXAMPLE,,10.00\n"
    response = client.post(
        "/api/upload?issuer=American%20Express&product=Platinum",
        files=[("files", ("statement.csv", payload, "text/csv"))],
    )

    assert response.status_code == 200
    assert response.json()["card"] == "americanexpress-platinum"
    statement = workspace["root"] / "inbox" / response.json()["written"][0]
    assert statement.exists()
    assert sidecar_path(statement).read_text() == '{"card_issuer": "American Express", "card_product": "Platinum"}\n'
