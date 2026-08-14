"""Extract raw transactions from inbox statement files."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from rich.console import Console

from config.parsers import resolve_parser
from config.parsers.base import empty_frame
from src.normalize import normalize
from src import paths as path_config
from src.upload_context import read_upload_context, sidecar_path

console = Console()
SUPPORTED = {".csv", ".pdf"}
ARCHIVED_DIR = "_ingested"


@dataclass
class ExtractionResult:
    frame: pd.DataFrame
    manifest: pd.DataFrame
    errors: list[str] = field(default_factory=list)
    successful: list[tuple[str, Path]] = field(default_factory=list)


def iter_statement_files(inbox: Path | None = None) -> list[tuple[str, Path]]:
    """Return (card/issuer, path) pairs from inbox/<card>/*.{csv,pdf}."""
    found: list[tuple[str, Path]] = []
    root = inbox if inbox is not None else path_config.INBOX
    if not root.exists():
        return found
    for card_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if card_dir.name.startswith("_"):
            continue
        for path in sorted(card_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED:
                found.append((card_dir.name, path))
    for path in sorted(root.glob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED:
            found.append(("generic", path))
    return found


def document_id(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_statement(path: Path, *, inbox: Path, card: str) -> Path:
    """Move a processed statement (and its upload sidecar) out of the active queue."""
    dest_dir = inbox / ARCHIVED_DIR / card
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        stem, suffix = path.stem, path.suffix
        index = 1
        dest = dest_dir / f"{stem}-{index}{suffix}"
        while dest.exists():
            index += 1
            dest = dest_dir / f"{stem}-{index}{suffix}"
    if not path.exists():
        return dest
    sidecar = sidecar_path(path)
    path.replace(dest)
    if sidecar.exists():
        sidecar.replace(dest.with_name(f".{dest.name}.upload.json"))
    return dest


def _manifest_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        "document_id",
        "card",
        "source_file",
        "status",
        "row_count",
        "parser",
        "error",
        "processed_at",
    ]
    return pd.DataFrame(rows, columns=columns)


def extract_statements(inbox: Path | None = None) -> ExtractionResult:
    """Parse every active inbox document, keeping successes even when siblings fail.

    Identical document bytes are ignored after their first appearance in this
    run. Different documents may still overlap; normalization reconciles those
    transaction occurrences using their immutable transaction fingerprint.
    """
    root = inbox if inbox is not None else path_config.INBOX
    files = iter_statement_files(root)
    if not files:
        console.print(f"[yellow]No statement files found under {root}[/yellow]")
        return ExtractionResult(empty_frame(), _manifest_frame([]))

    frames: list[pd.DataFrame] = []
    manifest: list[dict] = []
    errors: list[str] = []
    successful: list[tuple[str, Path]] = []
    seen_documents: set[str] = set()
    now = datetime.now(timezone.utc).isoformat()

    for card, path in files:
        relative = str(path.relative_to(root))
        doc_id: str | None = None
        parser_name = ""
        try:
            doc_id = document_id(path)
            if doc_id in seen_documents:
                manifest.append(
                    {
                        "document_id": doc_id,
                        "card": card,
                        "source_file": relative,
                        "status": "duplicate_document",
                        "row_count": 0,
                        "parser": "",
                        "error": None,
                        "processed_at": now,
                    }
                )
                successful.append((card, path))
                console.print(f"[dim]Skipped duplicate document[/dim] {relative}")
                continue
            seen_documents.add(doc_id)
            parser = resolve_parser(card, path.suffix.lower())
            parser_name = getattr(parser, "__name__", "parser")
            frame = parser(path, card=card, metadata=read_upload_context(path))
            frame = frame.copy()
            if not frame.empty:
                normalize(frame.copy())
            frame["source_document_id"] = doc_id
            frame["source_file"] = relative
            frame["source_row"] = range(len(frame))
            frames.append(frame)
            successful.append((card, path))
            manifest.append(
                {
                    "document_id": doc_id,
                    "card": card,
                    "source_file": relative,
                    "status": "parsed",
                    "row_count": len(frame),
                    "parser": parser_name,
                    "error": None,
                    "processed_at": now,
                }
            )
            console.print(f"[green]Extracted[/green] {len(frame):4d} rows  {relative}")
        except Exception as exc:  # noqa: BLE001 — keep sibling documents moving
            message = f"{relative}: {type(exc).__name__}: {exc}"
            errors.append(message)
            manifest.append(
                {
                    "document_id": doc_id,
                    "card": card,
                    "source_file": relative,
                    "status": "failed",
                    "row_count": 0,
                    "parser": parser_name,
                    "error": message,
                    "processed_at": now,
                }
            )
            console.print(f"[red]Failed[/red] {message}")

    report = _manifest_frame(manifest)
    combined = pd.concat(frames, ignore_index=True) if frames else empty_frame()
    return ExtractionResult(combined, report, errors=errors, successful=successful)


def extract_all(inbox: Path | None = None) -> pd.DataFrame:
    """Compatibility wrapper returning only the extracted frame."""
    return extract_statements(inbox).frame
