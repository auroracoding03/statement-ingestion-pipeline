"""Manual recurring obligations — local-only, never written into the ledger.

Predictable non-card expenses (mortgages, insurance) are defined once and
confirmed month-by-month. Only manually confirmed payments count as spending.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import uuid
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from filelock import FileLock

from src import paths

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
NAME_MAX = 100


class ObligationError(ValueError):
    """Validation or integrity failure for the obligations feature."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _definitions_path() -> Path:
    return paths.MANUAL_OBLIGATIONS_PATH


def _occurrences_path() -> Path:
    return paths.OBLIGATION_OCCURRENCES_PATH


def _lock_path() -> Path:
    return paths.OBLIGATIONS_LOCK


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a same-directory temp file, then replace atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp.write(text)
        tmp.flush()
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _backup_if_exists(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))


def _with_lock(fn):
    """Decorator: hold the obligations file lock for the duration of fn."""

    def wrapped(*args, **kwargs):
        paths.ensure_dirs()
        lock = FileLock(str(_lock_path()), timeout=60.0)
        with lock:
            return fn(*args, **kwargs)

    return wrapped


def _parse_month(month: str) -> tuple[int, int]:
    if not MONTH_RE.match(month or ""):
        raise ObligationError(f"Invalid month '{month}'; expected YYYY-MM")
    year, mon = month.split("-")
    return int(year), int(mon)


def _due_date(month: str, due_day: int) -> date:
    year, mon = _parse_month(month)
    last = monthrange(year, mon)[1]
    day = min(int(due_day), last)
    return date(year, mon, day)


def _validate_definition_fields(data: dict, *, partial: bool = False) -> dict:
    out: dict = {}

    if "name" in data or not partial:
        name = str(data.get("name", "")).strip()
        if not name or len(name) > NAME_MAX:
            raise ObligationError(f"name must be 1–{NAME_MAX} characters")
        out["name"] = name

    if "category" in data or not partial:
        category = str(data.get("category", "")).strip()
        if not category:
            raise ObligationError("category is required")
        out["category"] = category

    if "subcategory" in data or not partial:
        out["subcategory"] = str(data.get("subcategory") or "").strip()

    if "expected_amount_cents" in data or not partial:
        try:
            cents = int(data["expected_amount_cents"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ObligationError("expected_amount_cents must be a positive integer") from exc
        if cents <= 0:
            raise ObligationError("expected_amount_cents must be a positive integer")
        out["expected_amount_cents"] = cents

    if "due_day" in data or not partial:
        try:
            due_day = int(data["due_day"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ObligationError("due_day must be an integer from 1 through 28") from exc
        if due_day < 1 or due_day > 28:
            raise ObligationError("due_day must be an integer from 1 through 28")
        out["due_day"] = due_day

    if "active" in data:
        out["active"] = bool(data["active"])

    return out


def load_obligations() -> list[dict]:
    path = _definitions_path()
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001
        raise ObligationError(f"Cannot parse obligations file {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ObligationError(f"Cannot parse obligations file {path}: expected a mapping")
    obligations = doc.get("obligations") or []
    if not isinstance(obligations, list):
        raise ObligationError(f"Cannot parse obligations file {path}: obligations must be a list")
    return list(obligations)


def _save_obligations(items: list[dict]) -> None:
    path = _definitions_path()
    sorted_items = sorted(items, key=lambda o: str(o.get("name", "")).lower())
    doc = {"version": 1, "obligations": sorted_items}
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    _backup_if_exists(path)
    atomic_write_text(path, text)


def load_occurrences() -> list[dict]:
    path = _occurrences_path()
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise ObligationError(f"Cannot parse occurrences file {path}: {exc}") from exc
    if not isinstance(data, list):
        raise ObligationError(f"Cannot parse occurrences file {path}: expected a JSON array")
    return list(data)


def _save_occurrences(items: list[dict]) -> None:
    path = _occurrences_path()
    sorted_items = sorted(
        items,
        key=lambda o: (str(o.get("month", "")), str(o.get("snapshot_name", "")).lower()),
    )
    text = json.dumps(sorted_items, indent=2, ensure_ascii=False) + "\n"
    _backup_if_exists(path)
    atomic_write_text(path, text)


@_with_lock
def create_obligation(data: dict) -> dict:
    fields = _validate_definition_fields(data)
    now = _now()
    entry = {
        "id": uuid.uuid4().hex,
        **fields,
        "active": True,
        "created_at": now,
        "updated_at": now,
    }
    items = load_obligations()
    items.append(entry)
    _save_obligations(items)
    return entry


@_with_lock
def update_obligation(obligation_id: str, data: dict) -> dict:
    items = load_obligations()
    for idx, entry in enumerate(items):
        if entry.get("id") != obligation_id:
            continue
        fields = _validate_definition_fields(data, partial=True)
        updated = {**entry, **fields, "id": entry["id"], "updated_at": _now()}
        # Preserve immutable timestamps / identity
        updated["created_at"] = entry.get("created_at") or updated["updated_at"]
        items[idx] = updated
        _save_obligations(items)
        return updated
    raise ObligationError(f"Unknown obligation_id {obligation_id}")


@_with_lock
def deactivate_obligation(obligation_id: str) -> dict:
    items = load_obligations()
    for idx, entry in enumerate(items):
        if entry.get("id") != obligation_id:
            continue
        updated = {**entry, "active": False, "updated_at": _now()}
        items[idx] = updated
        _save_obligations(items)
        return updated
    raise ObligationError(f"Unknown obligation_id {obligation_id}")


def _find_obligation(obligation_id: str) -> dict:
    for entry in load_obligations():
        if entry.get("id") == obligation_id:
            return entry
    raise ObligationError(f"Unknown obligation_id {obligation_id}")


def monthly_obligations(month: str, as_of: date | None = None) -> dict:
    """Generate the monthly checklist from active definitions + stored confirmations."""
    _parse_month(month)
    as_of = as_of or date.today()

    definitions = [o for o in load_obligations() if o.get("active", True)]
    by_id = {
        o["obligation_id"]: o
        for o in load_occurrences()
        if o.get("month") == month
    }

    items: list[dict] = []
    expected_total = 0
    paid_total = 0
    outstanding_total = 0
    overdue_count = 0

    for definition in sorted(definitions, key=lambda o: str(o.get("name", "")).lower()):
        oid = definition["id"]
        expected = int(definition["expected_amount_cents"])
        due = _due_date(month, int(definition["due_day"]))
        stored = by_id.get(oid)

        if stored and stored.get("status") == "paid":
            status = "paid"
            actual = stored.get("actual_amount_cents")
            paid_date = stored.get("paid_date")
            note = stored.get("note") or ""
            name = stored.get("snapshot_name") or definition["name"]
            category = stored.get("snapshot_category") or definition["category"]
            subcategory = stored.get("snapshot_subcategory") or definition.get("subcategory") or ""
            expected_snap = int(stored.get("snapshot_expected_amount_cents") or expected)
        elif stored and stored.get("status") == "skipped":
            status = "skipped"
            actual = None
            paid_date = None
            note = stored.get("note") or ""
            name = stored.get("snapshot_name") or definition["name"]
            category = stored.get("snapshot_category") or definition["category"]
            subcategory = stored.get("snapshot_subcategory") or definition.get("subcategory") or ""
            expected_snap = int(stored.get("snapshot_expected_amount_cents") or expected)
        else:
            status = "overdue" if as_of > due else "expected"
            actual = None
            paid_date = None
            note = ""
            name = definition["name"]
            category = definition["category"]
            subcategory = definition.get("subcategory") or ""
            expected_snap = expected

        amount_changed = (
            status == "paid"
            and actual is not None
            and int(actual) != expected_snap
        )

        expected_total += expected_snap
        if status == "paid" and actual is not None:
            paid_total += int(actual)
        if status in ("expected", "overdue"):
            outstanding_total += expected_snap
        if status == "overdue":
            overdue_count += 1

        items.append(
            {
                "obligation_id": oid,
                "name": name,
                "category": category,
                "subcategory": subcategory,
                "expected_amount_cents": expected_snap,
                "actual_amount_cents": actual,
                "due_date": due.isoformat(),
                "paid_date": paid_date,
                "status": status,
                "amount_changed": amount_changed,
                "note": note,
            }
        )

    return {
        "month": month,
        "expected_total_cents": expected_total,
        "paid_total_cents": paid_total,
        "outstanding_total_cents": outstanding_total,
        "overdue_count": overdue_count,
        "items": items,
    }


@_with_lock
def upsert_occurrence(obligation_id: str, month: str, data: dict) -> dict:
    _parse_month(month)
    definition = _find_obligation(obligation_id)

    status = data.get("status")
    if status not in ("paid", "skipped"):
        raise ObligationError("status must be 'paid' or 'skipped'")

    note = str(data.get("note") or "").strip()

    if status == "paid":
        try:
            actual = int(data["actual_amount_cents"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ObligationError("paid requires a positive actual_amount_cents") from exc
        if actual <= 0:
            raise ObligationError("paid requires a positive actual_amount_cents")

        paid_raw = data.get("paid_date")
        if paid_raw is None or paid_raw == "":
            raise ObligationError("paid requires paid_date")
        if isinstance(paid_raw, date):
            paid_date = paid_raw
        else:
            try:
                paid_date = date.fromisoformat(str(paid_raw)[:10])
            except ValueError as exc:
                raise ObligationError(f"Invalid paid_date '{paid_raw}'") from exc
        if paid_date.strftime("%Y-%m") != month:
            raise ObligationError(f"paid_date {paid_date.isoformat()} is outside month {month}")
        paid_date_str: str | None = paid_date.isoformat()
        actual_out: int | None = actual
    else:
        actual_out = None
        paid_date_str = None

    record = {
        "obligation_id": obligation_id,
        "month": month,
        "status": status,
        "actual_amount_cents": actual_out,
        "paid_date": paid_date_str,
        "note": note,
        "snapshot_name": definition["name"],
        "snapshot_category": definition["category"],
        "snapshot_subcategory": definition.get("subcategory") or "",
        "snapshot_expected_amount_cents": int(definition["expected_amount_cents"]),
        "updated_at": _now(),
    }

    items = [o for o in load_occurrences() if not (o.get("obligation_id") == obligation_id and o.get("month") == month)]
    items.append(record)
    _save_occurrences(items)
    return record


@_with_lock
def clear_occurrence(obligation_id: str, month: str) -> bool:
    _parse_month(month)
    # Ensure the obligation exists (even if inactive) so unknown IDs 404
    _find_obligation(obligation_id)
    items = load_occurrences()
    remaining = [
        o for o in items if not (o.get("obligation_id") == obligation_id and o.get("month") == month)
    ]
    if len(remaining) == len(items):
        return False
    _save_occurrences(remaining)
    return True


def paid_category_monthly() -> pd.DataFrame:
    """Paid manual occurrences rolled up by month and snapshot category (dollars)."""
    paid = [o for o in load_occurrences() if o.get("status") == "paid"]
    if not paid:
        return pd.DataFrame(columns=["month", "category", "total", "txn_count"])

    rows = []
    for o in paid:
        cents = o.get("actual_amount_cents")
        if cents is None:
            continue
        rows.append(
            {
                "month": o["month"],
                "category": o.get("snapshot_category") or "Uncategorized",
                "total": int(cents) / 100.0,
                "txn_count": 1,
            }
        )
    frame = pd.DataFrame(rows)
    return (
        frame.groupby(["month", "category"], as_index=False)
        .agg(total=("total", "sum"), txn_count=("txn_count", "sum"))
        .sort_values(["month", "category"])
        .reset_index(drop=True)
    )
