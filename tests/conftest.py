"""Global test safety net.

Import-time default paths used to leak writes into the real `config/` and
`data/` trees. These fixtures redirect both, and fail loudly if a write still
targets the checkout or desktop app data directories.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import src.paths as paths

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REAL_DATA_ROOTS = [(_REPO_ROOT / "data").resolve()]
_local_app_data = os.environ.get("LOCALAPPDATA")
if _local_app_data:
    _REAL_DATA_ROOTS.append((Path(_local_app_data) / "Statement Pipeline" / "data").resolve())

_DATA_PATH_NAMES = (
    "INBOX",
    "DATA",
    "LEDGER_PARQUET",
    "LEDGER_LOCK",
    "FINANCE_DB",
    "PROPOSALS_PARQUET",
    "AI_PROPOSALS_PARQUET",
    "AI_APPLICATIONS_PATH",
    "AI_SNAPSHOTS",
    "INGEST_MANIFEST",
    "TRANSACTION_SOURCES_PARQUET",
    "SUPPRESSED_TXN_PATH",
    "RECURRING_PARQUET",
    "RECONCILE_PARQUET",
    "EXPORT_DIR",
    "PENDING_UPLOADS",
)

_WRITE_ATTRS = (
    "atomic_write_bytes",
    "atomic_write_parquet",
    "atomic_write_csv",
    "atomic_copy_stream",
    "atomic_copy_file",
    "atomic_build_file",
    "atomic_replace_directory",
)


def _is_forbidden_data_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = Path(path)
    for root in _REAL_DATA_ROOTS:
        if resolved == root or root in resolved.parents:
            return True
    return False


def _write_destination(fn, args, kwargs) -> Path | None:
    name = getattr(fn, "__name__", "")
    if name in {"atomic_copy_file", "atomic_write_parquet", "atomic_write_csv"}:
        value = kwargs.get("target", args[1] if len(args) > 1 else None)
    elif name in {
        "atomic_write_bytes",
        "atomic_copy_stream",
        "atomic_build_file",
        "atomic_replace_directory",
    }:
        value = kwargs.get("target", args[0] if args else None)
    else:
        return None
    if value is None:
        return None
    return Path(value)


def _guard_writer(original):
    def wrapped(*args, **kwargs):
        destination = _write_destination(original, args, kwargs)
        if destination is not None and _is_forbidden_data_path(destination):
            raise AssertionError(
                f"Test attempted to write {destination} under a real data directory. "
                "Redirect LEDGER_PARQUET / DATA to tmp_path instead."
            )
        return original(*args, **kwargs)

    wrapped.__name__ = getattr(original, "__name__", "wrapped")
    wrapped.__wrapped__ = original
    return wrapped


def _data_path_values(inbox: Path, data: Path) -> dict[str, Path]:
    return {
        "INBOX": inbox,
        "DATA": data,
        "LEDGER_PARQUET": data / "ledger.parquet",
        "LEDGER_LOCK": data / "ledger.lock",
        "FINANCE_DB": data / "finance.duckdb",
        "PROPOSALS_PARQUET": data / "proposals.parquet",
        "AI_PROPOSALS_PARQUET": data / "ai_proposals.parquet",
        "AI_APPLICATIONS_PATH": data / "ai_applications.json",
        "AI_SNAPSHOTS": data / "ai_snapshots",
        "INGEST_MANIFEST": data / "ingestion_manifest.parquet",
        "TRANSACTION_SOURCES_PARQUET": data / "transaction_sources.parquet",
        "SUPPRESSED_TXN_PATH": data / "suppressed_txn_ids.parquet",
        "RECURRING_PARQUET": data / "recurring.parquet",
        "RECONCILE_PARQUET": data / "reconciliation.parquet",
        "EXPORT_DIR": data / "export",
        "PENDING_UPLOADS": data / "pending_uploads",
    }


@pytest.fixture(autouse=True)
def guard_real_config(tmp_path, monkeypatch, request):
    """Point config writes at a temp copy unless a test opts out.

    Tests that need the genuine config can request the `real_config` marker.
    """
    if request.node.get_closest_marker("real_config"):
        return

    config = tmp_path / "_guard_config"
    config.mkdir(exist_ok=True)

    for name in ("RULES_PATH", "MERCHANTS_PATH", "EXPECTED_RECURRING_PATH", "PUBLISH_PATH"):
        original = getattr(paths, name)
        target = config / original.name
        if original.exists():
            target.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(paths, name, target)


@pytest.fixture(autouse=True)
def guard_real_data(tmp_path, monkeypatch):
    """Point ledger and other data writes at tmp_path and reject real-data paths."""
    inbox = tmp_path / "_guard_inbox"
    data = tmp_path / "_guard_data"
    inbox.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    values = _data_path_values(inbox, data)

    for name, value in values.items():
        if hasattr(paths, name):
            monkeypatch.setattr(paths, name, value)

    for module_name, module in list(sys.modules.items()):
        if module is None:
            continue
        if module_name != "src.paths" and not module_name.startswith(("src.", "src", "config.")):
            continue
        for name, value in values.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value, raising=False)

    wrapped: dict[int, object] = {}
    for module_name, module in list(sys.modules.items()):
        if module is None:
            continue
        if not (
            module_name == "src.atomic"
            or module_name.startswith(("src.", "src", "tests.", "config."))
        ):
            continue
        for attr in _WRITE_ATTRS:
            current = getattr(module, attr, None)
            if current is None or not callable(current):
                continue
            replacement = wrapped.get(id(current))
            if replacement is None:
                replacement = _guard_writer(current)
                wrapped[id(current)] = replacement
                wrapped[id(replacement)] = replacement
            monkeypatch.setattr(module, attr, replacement, raising=False)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_config: allow the test to read the repository's real config files"
    )
