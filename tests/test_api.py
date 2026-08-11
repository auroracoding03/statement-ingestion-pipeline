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
    tags_path = config / "tags.yaml"
    tags_path.write_text(
        yaml.safe_dump(
            {
                "tags": [
                    {"id": "date", "label": "Date", "kind": "occasion"},
                    {"id": "gift", "label": "Gift", "kind": "occasion"},
                ]
            }
        )
    )
    card_products_path = config / "card_products.yaml"
    card_products_path.write_text(
        yaml.safe_dump(
            {
                "products": {
                    "American Express": ["Platinum", "Delta Gold", "Gold", "Delta SkyMiles"],
                    "Wells Fargo": ["Autograph Visa Signature"],
                    "Chase": [],
                    "Bank of America": [],
                    "Capital One": [],
                    "Generic": [],
                }
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
                "tags": [],
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
                "tags": ["date"],
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
    monkeypatch.setattr(paths_mod, "TAGS_PATH", tags_path)
    monkeypatch.setattr(paths_mod, "CARD_PRODUCTS_PATH", card_products_path)

    # These were bound into module namespaces at import time
    for module in (pipeline_mod, store_mod, api_app):
        monkeypatch.setattr(module, "LEDGER_PARQUET", ledger_path, raising=False)
    monkeypatch.setattr(pipeline_mod, "LEDGER_LOCK", data / "ledger.lock", raising=False)
    monkeypatch.setattr(pipeline_mod, "PROPOSALS_PARQUET", data / "proposals.parquet", raising=False)
    monkeypatch.setattr(api_app, "INBOX", tmp_path / "inbox", raising=False)
    monkeypatch.setattr(api_app, "PENDING_UPLOADS", tmp_path / "data" / "pending_uploads", raising=False)
    (tmp_path / "data" / "pending_uploads").mkdir()
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


def test_health_is_lightweight(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "version" in body


def test_status_reports_counts(client: TestClient):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ledger_exists"] is True
    assert body["counts"]["total"] == 2


def test_ai_setup_and_proposal_routes(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        api_app.ai_review,
        "ai_status",
        lambda warmup=False: {
            "available": True,
            "model_installed": True,
            "gpu_resident": True,
            "model": "qwen3.5:9b",
            "size_vram": 6_600_000_000,
            "message": "ready",
        },
    )
    monkeypatch.setattr(api_app.ai_review, "list_proposals", lambda **_kwargs: {"total": 0, "items": []})

    status = client.get("/api/ai/status?warmup=true")
    assert status.status_code == 200
    assert status.json()["gpu_resident"] is True

    proposals = client.get("/api/ai/proposals?status=pending&kind=merchant")
    assert proposals.status_code == 200
    assert proposals.json() == {"total": 0, "items": []}


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
    assert r.status_code == 200
    assert r.json()["total"] == 2

    r = client.get("/api/transactions?tag=date")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["tags"] == ["date"]


def test_tags_vocabulary_crud(client: TestClient):
    listed = client.get("/api/tags")
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["items"]} >= {"date", "gift"}

    created = client.post("/api/tags", json={"label": "London-Paris", "kind": "trip"})
    assert created.status_code == 200
    assert created.json()["tag"]["id"] == "london-paris"

    blocked = client.delete("/api/tags/date")
    assert blocked.status_code == 409

    removed = client.delete("/api/tags/london-paris")
    assert removed.status_code == 200


def test_add_primary_category(client: TestClient):
    r = client.post("/api/categories", json={"category": "Travel"})
    assert r.status_code == 200
    assert "Travel" in r.json()["categories"]
    assert "Travel" in r.json()["subcategories"]


def test_subcategory_vocabulary_crud(client: TestClient):
    listed = client.get("/api/rules").json()
    assert "Food" in listed["subcategories"]

    created = client.post(
        "/api/subcategories",
        json={"category": "Food", "subcategory": "Coffee"},
    )
    assert created.status_code == 200
    assert "Coffee" in created.json()["subcategories"]["Food"]

    rules = client.get("/api/rules").json()
    assert "Coffee" in rules["subcategories"]["Food"]

    queue = client.get("/api/review/queue").json()
    assert "Coffee" in queue["subcategories"]["Food"]


def test_review_queue_and_decision_creates_rule(client: TestClient, workspace: dict):
    queue = client.get("/api/review/queue").json()
    assert queue["total"] == 2
    assert "Food" in queue["categories"]

    r = client.post(
        f"/api/review/{workspace['coffee_id']}",
        json={
            "category": "Food",
            "subcategory": "Coffee",
            "tags": ["date"],
            "create_rule": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rule"] is not None
    # Walmart does not match the coffee regex rule.
    assert body["applied_txn_ids"] == []

    # Decision is persisted and drops out of the queue
    assert client.get("/api/review/queue").json()["total"] == 1
    txn = client.get(f"/api/transactions?q=coffee").json()["items"][0]
    assert txn["category"] == "Food"
    assert txn["tags"] == ["date"]
    rules_doc = client.get("/api/rules").json()
    assert any(r["category"] == "Food" for r in rules_doc["rules"])
    assert "Coffee" in rules_doc["subcategories"]["Food"]


def test_review_rule_reclassifies_matching_queue_siblings(client: TestClient, workspace: dict):
    """Saving a rule should clear other open review rows that match it."""
    sibling_id = make_txn_id("chase", "2026-02-01", 7.25, "LOCAL COFFEE ROASTERS DOWNTOWN")
    ledger_path = workspace["root"] / "data" / "ledger.parquet"
    ledger = pd.read_parquet(ledger_path)
    sibling = {
        "txn_id": sibling_id,
        "card": "chase",
        "posted_date": "2026-02-01",
        "amount": 7.25,
        "raw_description": "LOCAL COFFEE ROASTERS DOWNTOWN",
        "normalized_merchant": "LOCAL COFFEE ROASTERS DOWNTOWN",
        "canonical_merchant": None,
        "merchant_source": "none",
        "proposed_canonical": None,
        "source_file": "chase/2026-02.csv",
        "category": None,
        "subcategory": None,
        "tags": [],
        "classified_by": None,
        "proposed_category": None,
        "proposed_subcategory": None,
    }
    pd.concat([ledger, pd.DataFrame([sibling])], ignore_index=True).to_parquet(ledger_path, index=False)

    assert client.get("/api/review/queue").json()["total"] == 3

    r = client.post(
        f"/api/review/{workspace['coffee_id']}",
        json={
            "category": "Food",
            "subcategory": "Coffee",
            "create_rule": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rule"] is not None
    assert sibling_id in body["applied_txn_ids"]

    queue = client.get("/api/review/queue").json()
    assert queue["total"] == 1
    assert all(item["txn_id"] != sibling_id for item in queue["items"])

    sibling_txn = next(
        t for t in client.get("/api/transactions?q=coffee").json()["items"] if t["txn_id"] == sibling_id
    )
    assert sibling_txn["category"] == "Food"
    assert sibling_txn["subcategory"] == "Coffee"
    assert sibling_txn["classified_by"] == "rule"


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


def test_rules_patch_category_and_subcategory(client: TestClient):
    created = client.post(
        "/api/rules",
        json={"merchant_canonical": "GA Natural Gas", "category": "Utilities"},
    )
    assert created.status_code == 200

    patched = client.patch(
        "/api/rules/0",
        json={"category": "Utilities", "subcategory": "NaturalGas"},
    )
    assert patched.status_code == 200
    body = patched.json()["rule"]
    assert body["index"] == 0
    assert body["category"] == "Utilities"
    assert body["subcategory"] == "NaturalGas"
    assert body["match"]["merchant_canonical"] == "GA Natural Gas"

    listed = client.get("/api/rules").json()
    assert listed["rules"][0]["subcategory"] == "NaturalGas"
    assert "NaturalGas" in listed["subcategories"]["Utilities"]

    assert client.patch("/api/rules/999", json={"category": "Utilities"}).status_code == 404
    assert client.delete("/api/rules/0").status_code == 200


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


def test_staged_upload_detects_chase_csv_and_commits_without_manual_details(client: TestClient, workspace: dict):
    payload = b"Transaction Date,Post Date,Description,Category,Type,Amount\n2026-01-01,2026-01-02,Coffee,Food,Sale,-10.00\n"
    inspected = client.post("/api/uploads/inspect", files=[("files", ("statement.csv", payload, "text/csv"))])

    assert inspected.status_code == 200
    item = inspected.json()["items"][0]
    assert item["issuer"] == "Chase"
    assert item["needs_manual_details"] is False

    committed = client.post("/api/uploads/commit", json={"items": [{"token": item["token"]}]})
    assert committed.status_code == 200
    assert committed.json()["written"] == ["chase/statement.csv"]
    assert (workspace["root"] / "inbox" / "chase" / "statement.csv").exists()


def test_staged_amex_csv_requires_only_product_confirmation(client: TestClient, workspace: dict):
    payload = b"Date,Description,Card Member,Account #,Amount\n2026-01-01,Coffee,ALEX EXAMPLE,,10.00\n"
    inspected = client.post("/api/uploads/inspect", files=[("files", ("statement.csv", payload, "text/csv"))])

    item = inspected.json()["items"][0]
    assert item["issuer"] == "American Express"
    assert item["confidence"] == "product_required"

    committed = client.post("/api/uploads/commit", json={"items": [{"token": item["token"], "product": "Platinum"}]})
    assert committed.status_code == 200
    statement = workspace["root"] / "inbox" / "americanexpress-platinum" / "statement.csv"
    assert statement.exists()
    assert sidecar_path(statement).exists()


def test_card_products_list_and_append(client: TestClient):
    listed = client.get("/api/card-products")
    assert listed.status_code == 200
    assert "Platinum" in listed.json()["products"]["American Express"]

    created = client.post(
        "/api/card-products",
        json={"issuer": "American Express", "product": "Blue Cash Preferred"},
    )
    assert created.status_code == 200
    assert "Blue Cash Preferred" in created.json()["products"]["American Express"]

    again = client.get("/api/card-products")
    assert "Blue Cash Preferred" in again.json()["products"]["American Express"]


def test_amex_commit_rejects_unknown_product(client: TestClient):
    payload = b"Date,Description,Card Member,Account #,Amount\n2026-01-01,Coffee,ALEX EXAMPLE,,10.00\n"
    inspected = client.post("/api/uploads/inspect", files=[("files", ("statement.csv", payload, "text/csv"))])
    token = inspected.json()["items"][0]["token"]

    rejected = client.post(
        "/api/uploads/commit",
        json={"items": [{"token": token, "product": "Not A Real Card"}]},
    )
    assert rejected.status_code == 422
    assert "Unsupported American Express product" in rejected.json()["detail"]
