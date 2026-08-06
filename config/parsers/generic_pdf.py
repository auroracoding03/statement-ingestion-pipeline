"""Best-effort generic PDF statement parser via pdfplumber.

Issuer PDFs vary widely. This extracts table-like rows that look like
date + description + amount. Add a dedicated issuer PDF parser when needed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pdfplumber
import pandas as pd

from .base import finalize

DATE_RE = re.compile(r"^(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\s+(.+)$")
AMOUNT_RE = re.compile(r"(-?\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\(\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?\))\s*$")


def _parse_line(line: str) -> dict | None:
    line = " ".join(line.split())
    if not line:
        return None
    date_match = DATE_RE.match(line)
    if not date_match:
        return None
    rest = date_match.group(2)
    amount_match = AMOUNT_RE.search(rest)
    if not amount_match:
        return None
    amount_text = amount_match.group(1)
    desc = rest[: amount_match.start()].strip(" -")
    if not desc:
        return None
    return {
        "posted_date": date_match.group(1),
        "amount": amount_text,
        "raw_description": desc,
    }


def parse_generic_pdf(path: Path, card: str, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    rows: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # Prefer tables when present
            tables = page.extract_tables() or []
            for table in tables:
                for raw in table:
                    if not raw or len(raw) < 3:
                        continue
                    cells = [c for c in raw if c is not None]
                    if len(cells) < 3:
                        continue
                    joined = " ".join(str(c) for c in cells)
                    parsed = _parse_line(joined)
                    if parsed:
                        rows.append(parsed)
            text = page.extract_text() or ""
            for line in text.splitlines():
                parsed = _parse_line(line)
                if parsed:
                    rows.append(parsed)

    # De-dupe identical line parses from table+text double extraction
    if rows:
        frame = pd.DataFrame(rows).drop_duplicates()
        rows = frame.to_dict(orient="records")
    return finalize(rows, card=card, source_file=str(path), metadata=metadata)
