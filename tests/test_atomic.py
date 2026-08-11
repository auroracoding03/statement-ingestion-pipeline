"""Windows-safe atomic write primitives."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

import src.atomic as atomic_mod


def test_atomic_write_parquet_survives_windows_readonly_fsync(tmp_path: Path, monkeypatch) -> None:
    """Regression for ingest failing with ``OSError: [Errno 9] Bad file descriptor``.

    On Windows, ``os.fsync`` after reopening a parquet file read-only raises
    EBADF. The helper must use a write-capable handle or treat sync as best-effort.
    """
    calls: list[str] = []
    real_open = Path.open

    def tracking_open(self, mode="r", *args, **kwargs):
        calls.append(mode)
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)

    target = tmp_path / "ledger.parquet"
    frame = pd.DataFrame({"txn_id": ["a"], "amount": [1.25]})

    written = atomic_mod.atomic_write_parquet(frame, target)

    assert written == target
    assert target.exists()
    assert "rb+" in calls
    assert "rb" not in calls
    assert pd.read_parquet(target).iloc[0]["amount"] == pytest.approx(1.25)


def test_fsync_file_ignores_unsupported_handles(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "note.txt"
    path.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(atomic_mod.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError(9, "Bad file descriptor")))

    atomic_mod._fsync_file(path)


def test_directory_fsync_failure_is_ignored_for_windows_compatibility(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(atomic_mod.os, "open", lambda *_args: 123)
    monkeypatch.setattr(atomic_mod.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("Bad file descriptor")))
    close = Mock()
    monkeypatch.setattr(atomic_mod.os, "close", close)

    atomic_mod._fsync_directory(tmp_path)

    close.assert_called_once_with(123)
