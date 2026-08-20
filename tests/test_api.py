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
    monkeypatch.setattr(paths_mod, "BUDGET_PATH", config / "budget.yaml")
    expected_path = config / "expected_recurring.yaml"
    expected_path.write_text("bills: []\n")
    monkeypatch.setattr(paths_mod, "EXPECTED_RECURRING_PATH", expected_path)

    # These were bound into module namespaces at import time
    for module in (pipeline_mod, store_mod, api_app):
        monkeypatch.setattr(module, "LEDGER_PARQUET", ledger_path, raising=False)
    monkeypatch.setattr(pipeline_mod, "LEDGER_LOCK", data / "ledger.lock", raising=False)
    monkeypatch.setattr(pipeline_mod, "PROPOSALS_PARQUET", data / "proposals.parquet", raising=False)
    monkeypatch.setattr(pipeline_mod, "TRANSACTION_SOURCES_PARQUET", data / "transaction_sources.parquet", raising=False)
    monkeypatch.setattr(pipeline_mod, "SUPPRESSED_TXN_PATH", data / "suppressed_txn_ids.parquet", raising=False)
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
    assert body["cardholders"] == []
    assert body["version"]
    assert "last_statement_upload_at" in body


def test_overview_month_summarizes_latest_month(client: TestClient):
    r = client.get("/api/overview/month")
    assert r.status_code == 200
    body = r.json()
    assert body["month"] == "2026-01"
    assert body["months"] == ["2026-01"]
    assert body["charge_count"] == 2
    assert body["spend_total"] == 90.94
    assert body["gross_charges"] == 90.94
    assert body["returns_total"] == 0.0
    assert body["payments_total"] == 0.0
    assert body["income_total"] == 0.0
    assert body["surplus"] == -90.94
    assert body["spend_delta"] is None
    assert body["uncategorized_count"] == 2
    assert body["review_count"] == 2
    assert body["tagged"] == [{"id": "date", "label": "Date", "kind": "occasion", "total": 6.75}]
    assert body["budget_rows"] == []


def test_cards_coverage_lists_products(client: TestClient):
    r = client.get("/api/cards")
    assert r.status_code == 200
    body = r.json()
    assert "products" in body
    assert isinstance(body["products"], list)
    assert body["selected"] is None
    labels = {row["label"] for row in body["products"]}
    assert "American Express Platinum" in labels


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


def _ai_status_payload(**overrides):
    body = {
        "available": True,
        "model_installed": True,
        "gpu_resident": False,
        "model": "qwen3.5:9b",
        "host": "http://127.0.0.1:11434",
        "size_vram": 0,
        "message": "ready",
    }
    body.update(overrides)
    return body


def test_ai_start_when_already_online(client: TestClient, monkeypatch):
    spawned = []
    monkeypatch.setattr(api_app, "start_ollama_serve", lambda **_kwargs: spawned.append("called") or {"started": False, "available": True})
    monkeypatch.setattr(api_app.ai_review, "ai_status", lambda warmup=False: _ai_status_payload())

    response = client.post("/api/ai/start")
    assert response.status_code == 200
    body = response.json()
    assert body["started"] is False
    assert body["available"] is True
    assert spawned == ["called"]


def test_ai_start_missing_binary_is_404(client: TestClient, monkeypatch):
    def missing(**_kwargs):
        raise FileNotFoundError("Ollama is not installed.")

    monkeypatch.setattr(api_app, "start_ollama_serve", missing)
    response = client.post("/api/ai/start")
    assert response.status_code == 404
    assert "not installed" in response.json()["detail"]


def test_ai_start_spawn_success(client: TestClient, monkeypatch):
    monkeypatch.setattr(api_app, "start_ollama_serve", lambda **_kwargs: {"started": True, "available": True})
    monkeypatch.setattr(api_app.ai_review, "ai_status", lambda warmup=False: _ai_status_payload())

    response = client.post("/api/ai/start")
    assert response.status_code == 200
    body = response.json()
    assert body["started"] is True
    assert body["available"] is True


def test_ai_start_timeout_is_503(client: TestClient, monkeypatch):
    def timed_out(**_kwargs):
        raise TimeoutError("Ollama did not become reachable. Check that it is installed and try again.")

    monkeypatch.setattr(api_app, "start_ollama_serve", timed_out)
    response = client.post("/api/ai/start")
    assert response.status_code == 503
    assert "did not become reachable" in response.json()["detail"]


def test_ai_start_rejects_remote_host(client: TestClient, monkeypatch):
    spawned = []
    monkeypatch.setattr(api_app, "recommended_config", lambda: {"host": "http://8.8.8.8:11434", "model": "qwen3.5:9b"})
    monkeypatch.setattr(api_app, "start_ollama_serve", lambda **kwargs: spawned.append(kwargs) or {"started": True, "available": True})

    response = client.post("/api/ai/start")
    assert response.status_code == 422
    assert "loopback" in response.json()["detail"].lower()
    assert spawned == []


def test_updates_are_exposed_without_network_access(client: TestClient):
    r = client.get("/api/updates")

    assert r.status_code == 200
    assert r.json()["supported"] is False


def test_transactions_search_and_filter(client: TestClient):
    r = client.get("/api/transactions?q=coffee")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    cards = client.get("/api/transactions?account_kind=card")
    banks = client.get("/api/transactions?account_kind=bank")
    assert cards.status_code == 200
    assert cards.json()["total"] > 0
    assert banks.json()["total"] == 0
    assert "spend_total" in cards.json()
    assert "income_total" in cards.json()

    r = client.get("/api/transactions?card=nope")
    assert r.json()["total"] == 0

    r = client.get("/api/transactions?unclassified=true")
    assert r.status_code == 200
    assert r.json()["total"] == 2

    r = client.get("/api/transactions?tag=date")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["tags"] == ["date"]


def test_transactions_filter_by_subcategory(client: TestClient, workspace: dict):
    client.post(
        "/api/transactions/bulk",
        json={"txn_ids": [workspace["coffee_id"]], "category": "Food", "subcategory": "Coffee"},
    )
    matched = client.get("/api/transactions?category=Food&subcategory=Coffee")
    assert matched.status_code == 200
    assert matched.json()["total"] == 1
    assert matched.json()["items"][0]["txn_id"] == workspace["coffee_id"]

    missed = client.get("/api/transactions?subcategory=Groceries")
    assert missed.json()["total"] == 0


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


def test_transactions_filter_sort_and_bulk(client: TestClient, workspace: dict):
    since = client.get("/api/transactions?since=2026-01-07").json()
    assert since["total"] == 1
    assert since["items"][0]["txn_id"] == workspace["coffee_id"]

    ranked = client.get("/api/transactions?sort=amount&order=desc").json()
    assert [row["amount"] for row in ranked["items"]] == [84.19, 6.75]

    overview = client.get("/api/overview/month?preset=t12m").json()
    assert overview["preset"] == "t12m"
    assert overview["since"] == "2025-02-01"
    assert overview["until"] == "2026-01-31"
    assert overview["spend_total"] == 90.94

    bulk = client.post(
        "/api/transactions/bulk",
        json={
            "txn_ids": [workspace["walmart_id"], workspace["coffee_id"]],
            "category": "Shopping",
            "subcategory": "Warehouse",
        },
    )
    assert bulk.status_code == 200
    assert bulk.json()["count"] == 2
    walmart = client.get("/api/transactions?q=wal-mart").json()["items"][0]
    assert walmart["category"] == "Shopping"
    assert walmart["classified_by"] == "manual"


def test_review_clusters_and_preview_do_not_write(client: TestClient, workspace: dict):
    clusters = client.get("/api/review/clusters").json()
    merchants = {item["merchant"] for item in clusters["items"]}
    assert "LOCAL COFFEE ROASTERS DOWNTOWN" in merchants
    coffee = next(item for item in clusters["items"] if "COFFEE" in item["merchant"])
    assert coffee["count"] == 1

    before_rules = (workspace["root"] / "config" / "rules.yaml").read_text()
    preview = client.post(
        "/api/review/preview-rule",
        json={"txn_id": workspace["coffee_id"], "category": "Food", "subcategory": "Coffee"},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["match_count"] == 1
    assert body["sample"][0]["txn_id"] == workspace["coffee_id"]
    assert (workspace["root"] / "config" / "rules.yaml").read_text() == before_rules
    assert client.get("/api/review/queue").json()["total"] == 2


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


def test_job_progress_is_returned_from_get(client: TestClient):
    from src.api import jobs

    job_id = jobs.create_job("ai-analyze")
    jobs.set_progress(job_id, 3, 8, "Merchant profiles 3/8")
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["id"] == job_id
    assert body["status"] == "pending"
    assert body["progress"] == {"current": 3, "total": 8, "message": "Merchant profiles 3/8"}


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
    assert item["account_kind"] == "card"

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
    assert item["needs_cardholder"] is False
    assert item["account_kind"] == "card"

    committed = client.post("/api/uploads/commit", json={"items": [{"token": item["token"], "product": "Platinum"}]})
    assert committed.status_code == 200
    statement = workspace["root"] / "inbox" / "americanexpress-platinum" / "statement.csv"
    assert statement.exists()
    assert sidecar_path(statement).exists()


def test_staged_amex_csv_without_names_requires_cardholder(client: TestClient, workspace: dict):
    payload = b"Date,Description,Card Member,Account #,Amount\n2026-01-01,Coffee,,,10.00\n"
    inspected = client.post("/api/uploads/inspect", files=[("files", ("statement.csv", payload, "text/csv"))])

    item = inspected.json()["items"][0]
    assert item["needs_cardholder"] is True
    token = item["token"]

    rejected = client.post(
        "/api/uploads/commit",
        json={"items": [{"token": token, "product": "Delta Gold"}]},
    )
    assert rejected.status_code == 422
    assert "cardholder" in rejected.json()["detail"].lower()

    committed = client.post(
        "/api/uploads/commit",
        json={"items": [{"token": token, "product": "Delta Gold", "cardholder": "Alex Example"}]},
    )
    assert committed.status_code == 200
    statement = workspace["root"] / "inbox" / "americanexpress-delta-gold" / "statement.csv"
    assert '"cardholder": "Alex Example"' in sidecar_path(statement).read_text()


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


def test_card_products_delete_unused_in_use_and_unknown(client: TestClient, workspace: dict):
    unused = client.delete("/api/card-products", params={"issuer": "American Express", "product": "Gold"})
    assert unused.status_code == 200
    assert unused.json()["deleted"] == {"issuer": "American Express", "product": "Gold"}
    remaining = unused.json()["products"]["American Express"]
    assert "Gold" not in remaining
    assert "Platinum" in remaining

    missing = client.delete("/api/card-products", params={"issuer": "American Express", "product": "Gold"})
    assert missing.status_code == 404

    ledger = pipeline_mod.load_ledger()
    extra = pd.DataFrame(
        [
            {
                "txn_id": make_txn_id("americanexpress-platinum", "2026-03-04", 55.0, "PLAT DINNER"),
                "card": "americanexpress-platinum",
                "card_issuer": "American Express",
                "card_product": "Platinum",
                "cardholder": None,
                "posted_date": "2026-03-04",
                "amount": 55.0,
                "raw_description": "PLAT DINNER",
                "normalized_merchant": "PLAT DINNER",
                "canonical_merchant": None,
                "merchant_source": "none",
                "source_file": "amex/plat.csv",
            }
        ]
    )
    pipeline_mod.write_ledger(pipeline_mod._ensure_columns(pd.concat([ledger, extra], ignore_index=True)))

    blocked = client.delete("/api/card-products", params={"issuer": "amex", "product": "Platinum"})
    assert blocked.status_code == 409
    assert "still used" in blocked.json()["detail"]
    listed = client.get("/api/card-products").json()["products"]["American Express"]
    assert "Platinum" in listed

    unknown = client.delete("/api/card-products", params={"issuer": "American Express", "product": "Mystery Card"})
    assert unknown.status_code == 404


def test_staged_boa_pdf_requires_product_before_commit(client: TestClient):
    inspected = client.post(
        "/api/uploads/inspect",
        files=[("files", ("eStmt.pdf", b"%PDF-1.4\n%\n", "application/pdf"))],
    )
    assert inspected.status_code == 200
    token = inspected.json()["items"][0]["token"]

    rejected = client.post(
        "/api/uploads/commit",
        json={"items": [{"token": token, "issuer": "Bank of America"}]},
    )
    assert rejected.status_code == 422
    assert "card product" in rejected.json()["detail"].lower()

    committed = client.post(
        "/api/uploads/commit",
        json={
            "items": [
                {
                    "token": token,
                    "issuer": "Bank of America",
                    "product": "Customized Cash Rewards",
                    "cardholder": "Alex Example",
                }
            ]
        },
    )
    assert committed.status_code == 200
    assert committed.json()["written"] == ["bankofamerica-customized-cash-rewards/eStmt.pdf"]


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


def test_wells_account_history_csv_inspects_as_bank(client: TestClient):
    payload = (
        b'"DATE","DESCRIPTION","AMOUNT","CHECK #","STATUS"\n'
        b'"08/04/2026","HOA DUES","-388.00","","Posted"\n'
    )
    inspected = client.post("/api/uploads/inspect", files=[("files", ("Checking.csv", payload, "text/csv"))])
    assert inspected.status_code == 200
    item = inspected.json()["items"][0]
    assert item["issuer"] == "Wells Fargo"
    assert item["confidence"] == "product_required"
    assert item["needs_cardholder"] is True
    assert item["account_kind"] == "bank"
    assert "account product" in item["message"].lower()
    assert "account holder" in item["message"].lower()


def test_wells_account_history_rejects_card_product_and_accepts_checking(client: TestClient, workspace: dict):
    payload = (
        b'"DATE","DESCRIPTION","AMOUNT","CHECK #","STATUS"\n'
        b'"08/04/2026","HOA DUES","-388.00","","Posted"\n'
    )
    inspected = client.post("/api/uploads/inspect", files=[("files", ("Checking.csv", payload, "text/csv"))])
    token = inspected.json()["items"][0]["token"]

    as_card = client.post(
        "/api/uploads/commit",
        json={
            "items": [
                {
                    "token": token,
                    "product": "Autograph Visa Signature",
                    "cardholder": "Alex Example",
                }
            ]
        },
    )
    assert as_card.status_code == 422
    assert "account product" in as_card.json()["detail"].lower()

    created = client.post(
        "/api/card-products",
        json={"issuer": "Wells Fargo", "product": "Everyday Checking"},
    )
    assert created.status_code == 200

    missing_holder = client.post(
        "/api/uploads/commit",
        json={"items": [{"token": token, "product": "Everyday Checking"}]},
    )
    assert missing_holder.status_code == 422
    assert "account holder" in missing_holder.json()["detail"].lower()

    committed = client.post(
        "/api/uploads/commit",
        json={
            "items": [
                {
                    "token": token,
                    "product": "Everyday Checking",
                    "cardholder": "Alex Example",
                }
            ]
        },
    )
    assert committed.status_code == 200
    statement = workspace["root"] / "inbox" / "wellsfargo-everyday-checking" / "Checking.csv"
    assert statement.exists()
    assert '"cardholder": "Alex Example"' in sidecar_path(statement).read_text()


def test_card_csv_rejects_bank_product(client: TestClient):
    created = client.post(
        "/api/card-products",
        json={"issuer": "American Express", "product": "Advantage Checking"},
    )
    assert created.status_code == 200
    payload = b"Date,Description,Card Member,Account #,Amount\n2026-01-01,Coffee,ALEX EXAMPLE,,10.00\n"
    inspected = client.post("/api/uploads/inspect", files=[("files", ("statement.csv", payload, "text/csv"))])
    token = inspected.json()["items"][0]["token"]
    rejected = client.post(
        "/api/uploads/commit",
        json={"items": [{"token": token, "product": "Advantage Checking"}]},
    )
    assert rejected.status_code == 422
    assert "card product" in rejected.json()["detail"].lower()


def test_assign_cardholder_endpoint_updates_only_blank_rows(client: TestClient, workspace: dict):
    ledger = pipeline_mod.load_ledger()
    extra = pd.DataFrame(
        [
            {
                "txn_id": make_txn_id("americanexpress-delta-gold", "2026-01-05", 40.0, "FLIGHT"),
                "card": "americanexpress-delta-gold",
                "card_issuer": "American Express",
                "card_product": "Delta Gold",
                "cardholder": None,
                "posted_date": "2026-01-05",
                "amount": 40.0,
                "raw_description": "FLIGHT",
                "normalized_merchant": "FLIGHT",
                "canonical_merchant": None,
                "merchant_source": "none",
                "source_file": "amex/jan.csv",
            },
            {
                "txn_id": make_txn_id("americanexpress-delta-gold", "2026-03-01", 80.0, "SAM FLIGHT"),
                "card": "americanexpress-delta-gold",
                "card_issuer": "American Express",
                "card_product": "Delta Gold",
                "cardholder": "Sam Example",
                "posted_date": "2026-03-01",
                "amount": 80.0,
                "raw_description": "SAM FLIGHT",
                "normalized_merchant": "SAM FLIGHT",
                "canonical_merchant": None,
                "merchant_source": "none",
                "source_file": "amex/mar.csv",
            },
            {
                "txn_id": make_txn_id("americanexpress-platinum", "2026-03-04", 55.0, "PLAT DINNER"),
                "card": "americanexpress-platinum",
                "card_issuer": "American Express",
                "card_product": "Platinum",
                "cardholder": None,
                "posted_date": "2026-03-04",
                "amount": 55.0,
                "raw_description": "PLAT DINNER",
                "normalized_merchant": "PLAT DINNER",
                "canonical_merchant": None,
                "merchant_source": "none",
                "source_file": "amex/plat.csv",
            },
        ]
    )
    pipeline_mod.write_ledger(pipeline_mod._ensure_columns(pd.concat([ledger, extra], ignore_index=True)))

    missing = client.post(
        "/api/cards/cardholder",
        json={"issuer": "Chase", "product": "Sapphire Preferred", "cardholder": "Alex Example"},
    )
    assert missing.status_code == 422

    assigned = client.post(
        "/api/cards/cardholder",
        json={"issuer": "American Express", "product": "Delta Gold", "cardholder": "Alex Example"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["count"] == 1
    assert assigned.json()["cardholder"] == "Alex Example"

    cards = client.get("/api/cards").json()["products"]
    labels = {row["label"]: row["cardholder"] for row in cards}
    assert labels["American Express Delta Gold · Alex Example"] == "Alex Example"
    assert labels["American Express Delta Gold · Sam Example"] == "Sam Example"
    assert labels["American Express Platinum · Unassigned"] == "Unassigned"


def test_budget_get_merges_categories_and_put_round_trips(client: TestClient, workspace: dict):
    listed = client.get("/api/budget")
    assert listed.status_code == 200
    names = [env["category"] for env in listed.json()["envelopes"]]
    assert names == ["Food", "Shopping", "Utilities"]
    assert all(env["amount"] is None for env in listed.json()["envelopes"])

    saved = client.put(
        "/api/budget",
        json={
            "envelopes": [
                {
                    "category": "Food",
                    "amount": 800,
                    "show_on_overview": True,
                    "subcategories": [
                        {"subcategory": "Groceries", "amount": 600, "show_on_overview": True},
                        {"subcategory": "Dates", "amount": 50, "show_on_overview": False},
                    ],
                },
                {"category": "Shopping", "amount": None, "show_on_overview": False, "subcategories": []},
                {"category": "Utilities", "amount": 200, "show_on_overview": False, "subcategories": []},
            ]
        },
    )
    assert saved.status_code == 200
    food = next(env for env in saved.json()["envelopes"] if env["category"] == "Food")
    assert food["amount"] == 800.0
    assert food["show_on_overview"] is True
    assert [sub["subcategory"] for sub in food["subcategories"]] == ["Groceries", "Dates"]

    rules = yaml.safe_load((workspace["root"] / "config" / "rules.yaml").read_text())
    assert "Dates" in (rules.get("subcategories") or {}).get("Food", [])

    again = client.get("/api/budget")
    food = next(env for env in again.json()["envelopes"] if env["category"] == "Food")
    assert food["amount"] == 800.0
    utilities = next(env for env in again.json()["envelopes"] if env["category"] == "Utilities")
    assert utilities["amount"] == 200.0
    assert utilities["show_on_overview"] is False


def test_patch_merchant_renames_aliases_and_applies_category(client: TestClient):
    client.post(
        "/api/rules",
        json={"merchant_canonical": "Walmart", "category": "Shopping", "subcategory": "Retail"},
    )
    patched = client.patch(
        "/api/merchants/Walmart",
        json={
            "canonical": "Walmart Supercenter",
            "aliases": [{"regex": r"(?i)wal[-\s]?mart"}, {"exact": "WMT"}],
            "category": "Shopping",
            "subcategory": "Retail",
            "apply_category": True,
            "restamp": True,
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["merchant"]["canonical"] == "Walmart Supercenter"
    assert body["applied"] == 1
    names = {item["canonical"] for item in client.get("/api/merchants").json()["items"]}
    assert "Walmart Supercenter" in names
    assert "Walmart" not in names

    txn = client.get("/api/transactions?q=wal").json()["items"][0]
    assert txn["canonical_merchant"] == "Walmart Supercenter"
    assert txn["category"] == "Shopping"
    assert txn["subcategory"] == "Retail"
    assert txn["classified_by"] == "manual"

    rules = client.get("/api/rules").json()["rules"]
    assert any(rule["match"].get("merchant_canonical") == "Walmart Supercenter" for rule in rules)
    assert not any(rule["match"].get("merchant_canonical") == "Walmart" for rule in rules)


def test_patch_merchant_unknown_is_404(client: TestClient):
    missing = client.patch("/api/merchants/Nope", json={"canonical": "Nope"})
    assert missing.status_code == 404


def test_category_impact_and_unassign_subcategory(client: TestClient, workspace: dict):
    client.post("/api/subcategories", json={"category": "Food", "subcategory": "Coffee"})
    client.post("/api/subcategories", json={"category": "Food", "subcategory": "Groceries"})
    client.post(
        "/api/transactions/bulk",
        json={"txn_ids": [workspace["coffee_id"]], "category": "Food", "subcategory": "Coffee"},
    )
    client.post(
        "/api/rules",
        json={"merchant_canonical": "Local Coffee Roasters", "category": "Food", "subcategory": "Coffee"},
    )
    client.patch(
        "/api/merchants/Walmart",
        json={"category": "Food", "subcategory": "Coffee", "apply_category": False, "restamp": False},
    )
    (workspace["root"] / "config" / "expected_recurring.yaml").write_text(
        yaml.safe_dump(
            {
                "bills": [
                    {
                        "name": "Coffee club",
                        "category": "Food",
                        "subcategory": "Coffee",
                        "merchant_regex": "(?i)coffee",
                    }
                ]
            }
        )
    )

    impact = client.get("/api/categories/impact", params={"category": "Food", "subcategory": "Coffee"})
    assert impact.status_code == 200
    body = impact.json()
    assert body["txn_count"] == 1
    assert body["rule_count"] == 1
    assert body["merchant_count"] == 1
    assert body["bill_count"] == 1

    deleted = client.post(
        "/api/categories/delete",
        json={"category": "Food", "subcategory": "Coffee", "action": "unassign"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["rewritten"] == 1

    txn = client.get("/api/transactions?q=coffee").json()["items"][0]
    assert txn["category"] == "Uncategorized"
    assert txn["subcategory"] in ("", None)
    assert txn["classified_by"] is None

    rules = client.get("/api/rules").json()
    assert "Food" in rules["categories"]
    assert "Coffee" not in (rules["subcategories"].get("Food") or [])
    assert "Groceries" in rules["subcategories"]["Food"]
    assert not any(rule.get("subcategory") == "Coffee" for rule in rules["rules"])

    walmart = next(item for item in client.get("/api/merchants").json()["items"] if item["canonical"] == "Walmart")
    assert walmart["category"] in (None, "")
    bills = yaml.safe_load((workspace["root"] / "config" / "expected_recurring.yaml").read_text())
    assert bills["bills"][0]["category"] in (None, "")


def test_category_delete_reassign_primary(client: TestClient, workspace: dict):
    client.post("/api/subcategories", json={"category": "Food", "subcategory": "Coffee"})
    client.post(
        "/api/transactions/bulk",
        json={"txn_ids": [workspace["coffee_id"]], "category": "Food", "subcategory": "Coffee"},
    )
    client.post(
        "/api/rules",
        json={"merchant_canonical": "Local Coffee Roasters", "category": "Food", "subcategory": "Coffee"},
    )
    client.patch(
        "/api/merchants/Walmart",
        json={"category": "Food", "apply_category": False, "restamp": False},
    )
    client.put(
        "/api/budget",
        json={
            "envelopes": [
                {"category": "Food", "amount": 800, "show_on_overview": True, "subcategories": []},
                {"category": "Shopping", "amount": None, "show_on_overview": False, "subcategories": []},
                {"category": "Utilities", "amount": 200, "show_on_overview": False, "subcategories": []},
            ]
        },
    )

    deleted = client.post(
        "/api/categories/delete",
        json={"category": "Food", "action": "reassign", "reassign_category": "Shopping"},
    )
    assert deleted.status_code == 200

    txn = client.get("/api/transactions?q=coffee").json()["items"][0]
    assert txn["category"] == "Shopping"
    assert txn["classified_by"] == "manual"

    rules = client.get("/api/rules").json()
    assert "Food" not in rules["categories"]
    assert "Shopping" in rules["categories"]
    assert "Food" not in rules["subcategories"]
    assert any(rule["category"] == "Shopping" for rule in rules["rules"] if rule["match"].get("merchant_canonical") == "Local Coffee Roasters")

    walmart = next(item for item in client.get("/api/merchants").json()["items"] if item["canonical"] == "Walmart")
    assert walmart["category"] == "Shopping"

    envelopes = {env["category"] for env in client.get("/api/budget").json()["envelopes"]}
    assert "Food" not in envelopes
    assert "Shopping" in envelopes


def test_cannot_delete_uncategorized(client: TestClient):
    created = client.post("/api/categories", json={"category": "Uncategorized"})
    assert created.status_code == 200
    refused = client.post(
        "/api/categories/delete",
        json={"category": "Uncategorized", "action": "unassign"},
    )
    assert refused.status_code == 422
    assert "Uncategorized" in client.get("/api/rules").json()["categories"]


def test_category_delete_unknown_is_404(client: TestClient):
    missing = client.post(
        "/api/categories/delete",
        json={"category": "NoSuch", "action": "unassign"},
    )
    assert missing.status_code == 404


def test_merge_yaml_merchants_rewrites_rules(client: TestClient):
    created = client.post(
        "/api/merchants",
        json={"canonical": "GPC", "aliases": [{"regex": "(?i)gpc paymentus"}], "restamp": False},
    )
    assert created.status_code == 200
    client.post("/api/rules", json={"merchant_canonical": "GPC", "category": "Utilities"})

    merged = client.post(
        "/api/merchants/merge",
        json={"source": "GPC", "target": "Walmart", "apply_category": False},
    )
    assert merged.status_code == 200
    body = merged.json()
    assert body["merchant"]["canonical"] == "Walmart"
    assert body["rewritten"] == 0
    assert body["applied"] == 0
    names = {item["canonical"] for item in client.get("/api/merchants").json()["items"]}
    assert "GPC" not in names
    walmart = next(item for item in client.get("/api/merchants").json()["items"] if item["canonical"] == "Walmart")
    assert any("gpc" in str(alias.get("regex") or "").lower() for alias in walmart["aliases"])

    rules = client.get("/api/rules").json()["rules"]
    assert any(rule["match"].get("merchant_canonical") == "Walmart" for rule in rules)
    assert not any(rule["match"].get("merchant_canonical") == "GPC" for rule in rules)


def test_orphans_listed_after_delete_and_merge_applies_target_category_only(
    client: TestClient, workspace: dict
):
    client.post("/api/merchants/recanonicalize")
    client.post(
        "/api/transactions/bulk",
        json={"txn_ids": [workspace["walmart_id"]], "category": "Food", "subcategory": "Groceries"},
    )
    stamped = client.post(
        "/api/merchants",
        json={
            "canonical": "GPC",
            "members": ["LOCAL COFFEE ROASTERS DOWNTOWN"],
            "restamp": True,
        },
    )
    assert stamped.status_code == 200
    deleted = client.delete("/api/merchants/GPC")
    assert deleted.status_code == 200

    listing = client.get("/api/merchants").json()
    orphans = {item["canonical"]: item for item in listing["orphans"]}
    assert "GPC" in orphans
    assert orphans["GPC"]["txn_count"] == 1
    assert "Walmart" not in orphans

    merged = client.post(
        "/api/merchants/merge",
        json={"source": "GPC", "target": "Walmart", "apply_category": True},
    )
    assert merged.status_code == 200
    assert merged.json()["rewritten"] == 1
    assert merged.json()["applied"] == 1

    items = {row["txn_id"]: row for row in client.get("/api/transactions").json()["items"]}
    coffee = items[workspace["coffee_id"]]
    walmart = items[workspace["walmart_id"]]
    assert coffee["canonical_merchant"] == "Walmart"
    assert coffee["category"] == "Shopping"
    assert coffee["classified_by"] == "manual"
    assert coffee["merchant_source"] == "manual"
    assert walmart["canonical_merchant"] == "Walmart"
    assert walmart["category"] == "Food"
    assert walmart["subcategory"] == "Groceries"

    listing = client.get("/api/merchants").json()
    assert not any(item["canonical"] == "GPC" for item in listing["orphans"])


def test_merge_leave_categories_only_renames_canonical(client: TestClient):
    client.post(
        "/api/merchants",
        json={
            "canonical": "GPC",
            "members": ["LOCAL COFFEE ROASTERS DOWNTOWN"],
            "restamp": True,
        },
    )
    client.delete("/api/merchants/GPC")
    merged = client.post(
        "/api/merchants/merge",
        json={"source": "GPC", "target": "Walmart", "apply_category": False},
    )
    assert merged.status_code == 200
    assert merged.json()["rewritten"] == 1
    assert merged.json()["applied"] == 0
    coffee = client.get("/api/transactions?q=coffee").json()["items"][0]
    assert coffee["canonical_merchant"] == "Walmart"
    assert coffee["category"] in (None, "", "Uncategorized")
    assert coffee["merchant_source"] == "manual"


def test_merge_refusals(client: TestClient):
    self_merge = client.post(
        "/api/merchants/merge",
        json={"source": "Walmart", "target": "walmart", "apply_category": False},
    )
    assert self_merge.status_code == 422
    unknown = client.post(
        "/api/merchants/merge",
        json={"source": "GPC", "target": "Nope", "apply_category": False},
    )
    assert unknown.status_code == 404
    empty = client.post(
        "/api/merchants/merge",
        json={"source": "GPC", "target": "", "apply_category": False},
    )
    assert empty.status_code == 422


def test_delete_transaction_removes_row(client: TestClient, workspace: dict):
    coffee_id = workspace["coffee_id"]
    deleted = client.delete(f"/api/transactions/{coffee_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "txn_id": coffee_id}

    listing = client.get("/api/transactions").json()
    assert listing["total"] == 1
    assert all(row["txn_id"] != coffee_id for row in listing["items"])


def test_delete_unknown_transaction_is_404(client: TestClient):
    missing = client.delete("/api/transactions/not-a-real-id")
    assert missing.status_code == 404


def test_categories_monthly_splits_subcategory_and_cardholder(client: TestClient, workspace: dict):
    ledger_path = workspace["root"] / "data" / "ledger.parquet"
    ledger = pd.read_parquet(ledger_path)
    extra = pd.DataFrame(
        [
            {
                "txn_id": make_txn_id("amex", "2026-05-10", 400.0, "HOTEL DOWNTOWN"),
                "card": "amex",
                "posted_date": "2026-05-10",
                "amount": 400.0,
                "raw_description": "HOTEL DOWNTOWN",
                "normalized_merchant": "HOTEL DOWNTOWN",
                "canonical_merchant": None,
                "merchant_source": "none",
                "proposed_canonical": None,
                "source_file": "amex/2026-05.csv",
                "category": "Travel",
                "subcategory": "Lodging",
                "tags": [],
                "classified_by": "manual",
                "proposed_category": None,
                "proposed_subcategory": None,
                "cardholder": "Alex Example",
            },
            {
                "txn_id": make_txn_id("amex", "2026-06-08", 200.0, "AIRLINE TICKET"),
                "card": "amex",
                "posted_date": "2026-06-08",
                "amount": 200.0,
                "raw_description": "AIRLINE TICKET",
                "normalized_merchant": "AIRLINE TICKET",
                "canonical_merchant": None,
                "merchant_source": "none",
                "proposed_canonical": None,
                "source_file": "amex/2026-06.csv",
                "category": "Travel",
                "subcategory": "Transit",
                "tags": [],
                "classified_by": "manual",
                "proposed_category": None,
                "proposed_subcategory": None,
                "cardholder": "Alex Example",
            },
            {
                "txn_id": make_txn_id("amex", "2026-06-09", 80.0, "AIRBNB"),
                "card": "amex",
                "posted_date": "2026-06-09",
                "amount": 80.0,
                "raw_description": "AIRBNB",
                "normalized_merchant": "AIRBNB",
                "canonical_merchant": None,
                "merchant_source": "none",
                "proposed_canonical": None,
                "source_file": "amex/2026-06.csv",
                "category": "Travel",
                "subcategory": "Lodging",
                "tags": [],
                "classified_by": "manual",
                "proposed_category": None,
                "proposed_subcategory": None,
                "cardholder": "Sam Example",
            },
        ]
    )
    pd.concat([ledger, extra], ignore_index=True).to_parquet(ledger_path, index=False)

    body = client.get("/api/categories/monthly").json()
    travel = [row for row in body if row["category"] == "Travel"]
    assert {(row["month"], row["subcategory"], round(row["total"], 2)) for row in travel} == {
        ("2026-05", "Lodging", 400.0),
        ("2026-06", "Transit", 200.0),
        ("2026-06", "Lodging", 80.0),
    }

    alex = client.get("/api/categories/monthly", params={"cardholder": "Alex Example"}).json()
    alex_travel = [row for row in alex if row["category"] == "Travel"]
    assert {(row["month"], row["subcategory"], round(row["total"], 2)) for row in alex_travel} == {
        ("2026-05", "Lodging", 400.0),
        ("2026-06", "Transit", 200.0),
    }


def _extra_txn(posted: str, amount: float, raw: str, **overrides) -> dict:
    card = overrides.get("card", "amex")
    row = {
        "txn_id": make_txn_id(card, posted, amount, raw),
        "card": card,
        "posted_date": posted,
        "amount": amount,
        "raw_description": raw,
        "normalized_merchant": overrides.get("normalized_merchant", raw),
        "canonical_merchant": overrides.get("canonical_merchant"),
        "merchant_source": "none",
        "proposed_canonical": None,
        "source_file": f"{card}/{posted[:7]}.csv",
        "category": overrides.get("category"),
        "subcategory": overrides.get("subcategory"),
        "tags": overrides.get("tags", []),
        "classified_by": overrides.get("classified_by"),
        "proposed_category": None,
        "proposed_subcategory": None,
    }
    row.update(overrides)
    row["txn_id"] = make_txn_id(row["card"], row["posted_date"], row["amount"], row["raw_description"])
    return row


def _write_extra(workspace: dict, rows: list[dict]) -> None:
    ledger_path = workspace["root"] / "data" / "ledger.parquet"
    ledger = pd.read_parquet(ledger_path)
    pd.concat([ledger, pd.DataFrame(rows)], ignore_index=True).to_parquet(ledger_path, index=False)


def test_recurring_uses_live_ledger_without_build(client: TestClient, workspace: dict):
    _write_extra(
        workspace,
        [
            _extra_txn("2026-04-15", 15.99, "NETFLIX", category="Subscriptions", subcategory="Streaming"),
            _extra_txn("2026-07-15", 16.99, "NETFLIX", category="Subscriptions", subcategory="Streaming"),
            _extra_txn("2026-07-02", 9.99, "SPOTIFY", category="Subscriptions"),
            _extra_txn("2026-07-20", 9.99, "SPOTIFY", category="Subscriptions"),
        ],
    )
    assert not (workspace["root"] / "data" / "recurring.parquet").exists()

    body = client.get("/api/recurring").json()
    netflix = next(row for row in body if row["normalized_merchant"] == "NETFLIX")
    assert netflix["months"] == 4
    assert netflix["last_seen"] == "2026-07-15"
    assert netflix["last_amount"] == 16.99
    assert "price_hike" in str(netflix["flags"]).split(",")

    spotify = next(row for row in body if row["normalized_merchant"] == "SPOTIFY")
    assert spotify["months"] == 1
    assert spotify["last_seen"] == "2026-07-20"
    assert spotify["last_amount"] == 9.99


def test_manual_override_survives_classify(client: TestClient, workspace: dict):
    april = _extra_txn("2026-04-15", 15.99, "NETFLIX")
    july = _extra_txn("2026-07-15", 15.99, "NETFLIX")
    _write_extra(workspace, [april, july])

    classified = pipeline_mod.run_classify()
    assert classified.get("error") is None

    locked = client.post(
        "/api/transactions/bulk",
        json={"txn_ids": [april["txn_id"]], "category": "Food", "tags": ["gift"]},
    )
    assert locked.status_code == 200

    pipeline_mod.run_classify()
    listing = {row["txn_id"]: row for row in client.get("/api/transactions").json()["items"]}
    override = listing[april["txn_id"]]
    sibling = listing[july["txn_id"]]
    assert override["category"] == "Food"
    assert override["tags"] == ["gift"]
    assert override["classified_by"] == "manual"
    assert sibling["category"] == "Subscriptions"
    assert sibling["subcategory"] == "Streaming"
    assert sibling["classified_by"] == "rule"


def test_bulk_add_tags_keeps_rule_classification(client: TestClient, workspace: dict):
    row = _extra_txn("2026-04-15", 15.99, "NETFLIX")
    _write_extra(workspace, [row])
    classified = pipeline_mod.run_classify()
    assert classified.get("error") is None

    merged = client.post(
        "/api/transactions/bulk",
        json={"txn_ids": [row["txn_id"]], "add_tags": ["gift"]},
    )
    assert merged.status_code == 200
    listing = {item["txn_id"]: item for item in client.get("/api/transactions").json()["items"]}
    tagged = listing[row["txn_id"]]
    assert tagged["category"] == "Subscriptions"
    assert tagged["classified_by"] == "rule"
    assert "gift" in tagged["tags"]

    stamped = client.post(
        "/api/transactions/bulk",
        json={"txn_ids": [row["txn_id"]], "category": "Food"},
    )
    assert stamped.status_code == 200
    listing = {item["txn_id"]: item for item in client.get("/api/transactions").json()["items"]}
    food = listing[row["txn_id"]]
    assert food["category"] == "Food"
    assert food["classified_by"] == "manual"
    assert "gift" in food["tags"]

    empty = client.post("/api/transactions/bulk", json={"txn_ids": [row["txn_id"]]})
    assert empty.status_code == 400


def test_tag_spend_trip_buckets_exclude_untagged(client: TestClient, workspace: dict):
    created = client.post("/api/tags", json={"label": "2025-LondonToParis", "kind": "trip"})
    assert created.status_code == 200
    tag_id = created.json()["tag"]["id"]
    lodging = _extra_txn(
        "2025-06-10",
        420.0,
        "HOTEL PARIS",
        category="Travel",
        subcategory="Lodging",
        tags=[tag_id],
    )
    food = _extra_txn(
        "2025-06-11",
        85.5,
        "BISTRO",
        category="Food",
        subcategory="Dining",
        tags=[tag_id],
    )
    control = _extra_txn(
        "2025-06-12",
        50.0,
        "LOCAL CAFE",
        category="Food",
        subcategory="Dining",
    )
    _write_extra(workspace, [lodging, food, control])

    body = client.get("/api/tags/spend?kind=trip").json()
    trip = next(item for item in body["items"] if item["id"] == tag_id)
    assert trip["total"] == 505.5
    assert trip["txn_count"] == 2
    by_pair = {(row["category"], row["subcategory"]): row for row in trip["breakdown"]}
    assert by_pair[("Travel", "Lodging")]["total"] == 420.0
    assert by_pair[("Travel", "Lodging")]["txn_count"] == 1
    assert by_pair[("Food", "Dining")]["total"] == 85.5
    assert by_pair[("Food", "Dining")]["txn_count"] == 1
    assert all(item["kind"] == "trip" for item in body["items"])
    assert {item["id"] for item in body["items"]} == {tag_id}
