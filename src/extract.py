"""Extract raw transactions from inbox statement files."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from rich.console import Console

from config.parsers import resolve_parser
from config.parsers.base import empty_frame
from src.paths import INBOX
from src.upload_context import read_upload_context

console = Console()
SUPPORTED = {".csv", ".pdf"}


@dataclass
class ExtractionResult:
    frame: pd.DataFrame
    manifest: pd.DataFrame


class ExtractionError(RuntimeError):
    """A required input could not be parsed; no partial ledger may be committed."""

    def __init__(self, errors: list[str], manifest: pd.DataFrame):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.manifest = manifest


def iter_statement_files(inbox: Path = INBOX) -> list[tuple[str, Path]]:
    """Return (card/issuer, path) pairs from inbox/<card>/*.{csv,pdf}."""
    found: list[tuple[str, Path]] = []
    if not inbox.exists():
        return found
    for card_dir in sorted(p for p in inbox.iterdir() if p.is_dir()):
        if card_dir.name.startswith("_"):
            continue
        for path in sorted(card_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED:
                found.append((card_dir.name, path))
    for path in sorted(inbox.glob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED:
            found.append(("generic", path))
    return found


def document_id(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def extract_statements(inbox: Path = INBOX) -> ExtractionResult:
    """Extract every document as one all-or-nothing ingestion batch.

    Identical document bytes are ignored after their first appearance. Different
    documents may still overlap; normalization reconciles those transaction
    occurrences using their immutable transaction fingerprint.
    """
    files = iter_statement_files(inbox)
    if not files:
        console.print(f"[yellow]No statement files found under {inbox}[/yellow]")
        return ExtractionResult(empty_frame(), _manifest_frame([]))

    frames: list[pd.DataFrame] = []
    manifest: list[dict] = []
    errors: list[str] = []
    seen_documents: set[str] = set()
    now = datetime.now(timezone.utc).isoformat()

    for card, path in files:
        relative = str(path.relative_to(inbox))
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
                console.print(f"[dim]Skipped duplicate document[/dim] {relative}")
                continue
            seen_documents.add(doc_id)
            parser = resolve_parser(card, path.suffix.lower())
            parser_name = getattr(parser, "__name__", "parser")
            frame = parser(path, card=card, metadata=read_upload_context(path))
            frame = frame.copy()
            frame["source_document_id"] = doc_id
            frame["source_file"] = relative
            frame["source_row"] = range(len(frame))
            frames.append(frame)
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
        except Exception as exc:  # noqa: BLE001 — preserve diagnostic and abort the batch
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
    if errors:
        raise ExtractionError(errors, report)
    if not frames:
        return ExtractionResult(empty_frame(), report)
    return ExtractionResult(pd.concat(frames, ignore_index=True), report)


def extract_all(inbox: Path = INBOX) -> pd.DataFrame:
    """Compatibility wrapper returning only the extracted frame."""
    return extract_statements(inbox).frame
