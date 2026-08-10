"""Small, durable write primitives for local financial data."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from typing import BinaryIO
from pathlib import Path

import pandas as pd


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability.

    POSIX filesystems support syncing a directory after a rename. Windows does
    not, and calling ``fsync`` on its directory handle raises ``EBADF``. The
    file itself has already been synced before the rename, so skip only this
    unsupported final durability hint.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            return
    finally:
        os.close(fd)


def _temporary_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    return Path(name)


def atomic_write_bytes(target: Path, content: bytes) -> Path:
    """Write a complete file, then atomically replace its previous version."""
    temporary = _temporary_path(target)
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def atomic_write_text(target: Path, content: str) -> Path:
    return atomic_write_bytes(target, content.encode("utf-8"))


def atomic_copy_stream(target: Path, source: BinaryIO) -> Path:
    """Persist an uploaded source document without exposing a partial file."""
    temporary = _temporary_path(target)
    try:
        with temporary.open("wb") as handle:
            shutil.copyfileobj(source, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def atomic_copy_file(source: Path, target: Path) -> Path:
    with source.open("rb") as handle:
        return atomic_copy_stream(target, handle)


def atomic_write_parquet(frame: pd.DataFrame, target: Path) -> Path:
    temporary = _temporary_path(target)
    try:
        frame.to_parquet(temporary, index=False)
        _fsync_file(temporary)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def atomic_write_csv(frame: pd.DataFrame, target: Path) -> Path:
    temporary = _temporary_path(target)
    try:
        frame.to_csv(temporary, index=False)
        _fsync_file(temporary)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def atomic_build_file(target: Path, build: Callable[[Path], None]) -> Path:
    """Build an arbitrary file at a sibling path and publish it atomically."""
    temporary = _temporary_path(target)
    try:
        temporary.unlink(missing_ok=True)
        build(temporary)
        _fsync_file(temporary)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def atomic_replace_directory(target: Path, build: Callable[[Path], None]) -> Path:
    """Build a complete generated directory before making it visible.

    The target is generated output only. A short-lived sibling backup lets us
    restore the previous directory if the final rename fails.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.stage.", dir=target.parent))
    backup: Path | None = None
    try:
        build(stage)
        _fsync_directory(stage)
        if target.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{target.name}.backup.", dir=target.parent))
            backup.rmdir()
            os.replace(target, backup)
        try:
            os.replace(stage, target)
        except Exception:
            if backup is not None and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        _fsync_directory(target.parent)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if backup is not None and backup.exists():
            shutil.rmtree(backup)
    return target
