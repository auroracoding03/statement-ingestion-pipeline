"""Intentional Bank of America placeholders pending representative statements."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def parse_bank_of_america_placeholder(path: Path, card: str, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
    """Fail safely until regular and Air France statement layouts are sampled."""
    product = (metadata or {}).get("card_product") or card
    raise ValueError(
        f"Bank of America parser placeholder for {product!r}: upload a representative "
        "native-text statement before this document can be ingested"
    )
