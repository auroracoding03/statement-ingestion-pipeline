"""Manual recurring obligations — definitions, monthly confirmations, integrity."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

import src.paths as paths
from src.obligations import (
    ObligationError,
    clear_occurrence,
    create_obligation,
    deactivate_obligation,
    load_obligations,
    load_occurrences,
    monthly_obligations,
    update_obligation,
    upsert_occurrence,
)


@pytest.fixture
def obl_workspace(tmp_path: Path, monkeypatch) -> Path:
    config = tmp_path / "config"
    data = tmp_path / "data"
    config.mkdir()
    data.mkdir()
    defs = config / "manual_obligations.yaml"
    defs.write_text("version: 1\nobligations: []\n")
    monkeypatch.setattr(paths, "MANUAL_OBLIGATIONS_PATH", defs)
    monkeypatch.setattr(paths, "OBLIGATION_OCCURRENCES_PATH", data / "manual_obligation_occurrences.json")
    monkeypatch.setattr(paths, "OBLIGATIONS_LOCK", data / "manual_obligations.lock")
    monkeypatch.setattr(paths, "DATA", data)
    return tmp_path


def _mortgage(**overrides):
    base = {
        "name": "Mortgage 1",
        "category": "Housing",
        "subcategory": "Mortgage",
        "expected_amount_cents": 210000,
        "due_day": 1,
    }
    base.update(overrides)
    return base


def test_missing_files_load_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths, "MANUAL_OBLIGATIONS_PATH", tmp_path / "missing.yaml")
    monkeypatch.setattr(paths, "OBLIGATION_OCCURRENCES_PATH", tmp_path / "missing.json")
    assert load_obligations() == []
    assert load_occurrences() == []


def test_create_assigns_id_and_timestamps(obl_workspace: Path):
    entry = create_obligation(_mortgage())
    assert entry["id"]
    assert len(entry["id"]) == 32
    assert entry["created_at"]
    assert entry["updated_at"]
    assert entry["active"] is True
    assert load_obligations()[0]["id"] == entry["id"]


def test_update_cannot_change_id(obl_workspace: Path):
    entry = create_obligation(_mortgage())
    updated = update_obligation(entry["id"], {**_mortgage(name="Mortgage One"), "id": "hijacked"})
    assert updated["id"] == entry["id"]
    assert updated["name"] == "Mortgage One"


def test_deactivation_preserves_payment_records(obl_workspace: Path):
    entry = create_obligation(_mortgage())
    upsert_occurrence(
        entry["id"],
        "2026-08",
        {"status": "paid", "actual_amount_cents": 210000, "paid_date": "2026-08-01"},
    )
    deactivate_obligation(entry["id"])
    assert load_obligations()[0]["active"] is False
    assert len(load_occurrences()) == 1
    month = monthly_obligations("2026-08", as_of=date(2026, 8, 15))
    # Deactivated definitions no longer appear in the monthly checklist
    assert month["items"] == []
    # But the historical confirmation file still holds the payment
    assert load_occurrences()[0]["status"] == "paid"


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_amount_cents": 0},
        {"expected_amount_cents": -100},
        {"due_day": 0},
        {"due_day": 29},
        {"name": ""},
        {"name": "   "},
        {"category": ""},
    ],
)
def test_invalid_amounts_and_due_days_rejected(obl_workspace: Path, payload: dict):
    data = _mortgage(**payload)
    with pytest.raises(ObligationError):
        create_obligation(data)


def test_monthly_view_generates_expected(obl_workspace: Path):
    create_obligation(_mortgage())
    create_obligation(_mortgage(name="Car Insurance 1", category="Transport", due_day=15, expected_amount_cents=12000))
    month = monthly_obligations("2026-08", as_of=date(2026, 8, 1))
    assert month["month"] == "2026-08"
    assert len(month["items"]) == 2
    assert all(i["status"] == "expected" for i in month["items"])
    assert month["expected_total_cents"] == 222000
    assert month["paid_total_cents"] == 0
    assert month["outstanding_total_cents"] == 222000
    assert month["overdue_count"] == 0


def test_past_due_unconfirmed_becomes_overdue(obl_workspace: Path):
    create_obligation(_mortgage(due_day=1))
    month = monthly_obligations("2026-08", as_of=date(2026, 8, 5))
    assert month["items"][0]["status"] == "overdue"
    assert month["overdue_count"] == 1
    assert month["outstanding_total_cents"] == 210000


def test_future_rows_remain_expected(obl_workspace: Path):
    create_obligation(_mortgage(due_day=20))
    month = monthly_obligations("2026-08", as_of=date(2026, 8, 5))
    assert month["items"][0]["status"] == "expected"
    assert month["overdue_count"] == 0


def test_paid_requires_amount_and_date(obl_workspace: Path):
    entry = create_obligation(_mortgage())
    with pytest.raises(ObligationError):
        upsert_occurrence(entry["id"], "2026-08", {"status": "paid", "paid_date": "2026-08-01"})
    with pytest.raises(ObligationError):
        upsert_occurrence(entry["id"], "2026-08", {"status": "paid", "actual_amount_cents": 210000})


def test_paid_date_outside_month_rejected(obl_workspace: Path):
    entry = create_obligation(_mortgage())
    with pytest.raises(ObligationError, match="outside month"):
        upsert_occurrence(
            entry["id"],
            "2026-08",
            {"status": "paid", "actual_amount_cents": 210000, "paid_date": "2026-07-31"},
        )


def test_skipped_clears_amount_and_paid_date(obl_workspace: Path):
    entry = create_obligation(_mortgage())
    upsert_occurrence(
        entry["id"],
        "2026-08",
        {"status": "paid", "actual_amount_cents": 210000, "paid_date": "2026-08-01"},
    )
    record = upsert_occurrence(entry["id"], "2026-08", {"status": "skipped", "note": "vacation"})
    assert record["status"] == "skipped"
    assert record["actual_amount_cents"] is None
    assert record["paid_date"] is None
    month = monthly_obligations("2026-08", as_of=date(2026, 8, 15))
    assert month["items"][0]["status"] == "skipped"
    assert month["paid_total_cents"] == 0
    assert month["outstanding_total_cents"] == 0
    assert month["expected_total_cents"] == 210000


def test_reset_removes_only_that_month(obl_workspace: Path):
    entry = create_obligation(_mortgage())
    upsert_occurrence(
        entry["id"],
        "2026-07",
        {"status": "paid", "actual_amount_cents": 210000, "paid_date": "2026-07-01"},
    )
    upsert_occurrence(
        entry["id"],
        "2026-08",
        {"status": "paid", "actual_amount_cents": 210000, "paid_date": "2026-08-01"},
    )
    assert clear_occurrence(entry["id"], "2026-08") is True
    remaining = load_occurrences()
    assert len(remaining) == 1
    assert remaining[0]["month"] == "2026-07"


def test_amount_changed_when_actual_differs(obl_workspace: Path):
    entry = create_obligation(_mortgage())
    upsert_occurrence(
        entry["id"],
        "2026-08",
        {"status": "paid", "actual_amount_cents": 215000, "paid_date": "2026-08-01"},
    )
    month = monthly_obligations("2026-08", as_of=date(2026, 8, 15))
    assert month["items"][0]["amount_changed"] is True
    assert month["paid_total_cents"] == 215000


def test_one_occurrence_per_obligation_month(obl_workspace: Path):
    entry = create_obligation(_mortgage())
    upsert_occurrence(
        entry["id"],
        "2026-08",
        {"status": "paid", "actual_amount_cents": 210000, "paid_date": "2026-08-01"},
    )
    upsert_occurrence(
        entry["id"],
        "2026-08",
        {"status": "paid", "actual_amount_cents": 200000, "paid_date": "2026-08-02"},
    )
    matches = [o for o in load_occurrences() if o["month"] == "2026-08"]
    assert len(matches) == 1
    assert matches[0]["actual_amount_cents"] == 200000


def test_corrupt_primary_file_not_overwritten(obl_workspace: Path):
    path = paths.MANUAL_OBLIGATIONS_PATH
    path.write_text("not: [valid\n")
    with pytest.raises(ObligationError, match="Cannot parse"):
        create_obligation(_mortgage())
    assert "not: [valid" in path.read_text()


def test_successful_replacement_creates_backup(obl_workspace: Path):
    create_obligation(_mortgage())
    bak = paths.MANUAL_OBLIGATIONS_PATH.with_suffix(".yaml.bak")
    assert bak.exists()
    # Second write should refresh the backup from the previous good file
    create_obligation(_mortgage(name="Mortgage 2", expected_amount_cents=180000))
    assert bak.exists()
    prior = yaml.safe_load(bak.read_text())
    assert any(o["name"] == "Mortgage 1" for o in prior["obligations"])

    entry = load_obligations()[0]
    upsert_occurrence(
        entry["id"],
        "2026-08",
        {"status": "paid", "actual_amount_cents": 210000, "paid_date": "2026-08-01"},
    )
    occ_bak = paths.OBLIGATION_OCCURRENCES_PATH.with_suffix(".json.bak")
    # First write has nothing to back up; second write creates the backup
    upsert_occurrence(
        entry["id"],
        "2026-08",
        {"status": "skipped"},
    )
    assert occ_bak.exists()
