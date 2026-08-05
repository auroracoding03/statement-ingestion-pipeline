"""Extract raw transactions from inbox statement files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from rich.console import Console

from config.parsers import resolve_parser
from config.parsers.base import empty_frame
from src.paths import INBOX

console = Console()
SUPPORTED = {".csv", ".pdf"}


def iter_statement_files(inbox: Path = INBOX) -> list[tuple[str, Path]]:
    """Return (card/issuer, path) pairs from inbox/<card>/*.{csv,pdf}."""
    found: list[tuple[str, Path]] = []
    if not inbox.exists():
        return found
    for card_dir in sorted(p for p in inbox.iterdir() if p.is_dir()):
        for path in sorted(card_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED:
                found.append((card_dir.name, path))
    # Also allow loose files directly under inbox/
    for path in sorted(inbox.glob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED:
            found.append(("generic", path))
    return found


def extract_all(inbox: Path = INBOX) -> pd.DataFrame:
    files = iter_statement_files(inbox)
    if not files:
        console.print(f"[yellow]No statement files found under {inbox}[/yellow]")
        return empty_frame()

    frames: list[pd.DataFrame] = []
    for card, path in files:
        suffix = path.suffix.lower()
        try:
            parser = resolve_parser(card, suffix)
            frame = parser(path, card=card)
            console.print(f"[green]Extracted[/green] {len(frame):4d} rows  {path.relative_to(inbox)}")
            frames.append(frame)
        except Exception as exc:  # noqa: BLE001 — surface per-file failures, keep going
            console.print(f"[red]Failed[/red] {path}: {exc}")

    if not frames:
        return empty_frame()
    return pd.concat(frames, ignore_index=True)
