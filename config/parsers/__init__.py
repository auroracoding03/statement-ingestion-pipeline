"""Issuer-specific statement parsers.

Add a module per issuer under this package and register it in PARSER_REGISTRY.
Layout convention for inbox/:
  inbox/<card_or_issuer>/<YYYY-MM>.csv|pdf
"""

from __future__ import annotations

from .amex_csv import parse_amex_csv
from .capital_one_pdf import parse_capital_one_pdf
from .chase_csv import parse_chase_csv
from .chase_pdf import parse_chase_pdf
from .generic_csv import parse_generic_csv
from .generic_pdf import parse_generic_pdf

# Keys are lowercase folder / issuer names under inbox/
PARSER_REGISTRY = {
    "chase": {
        ".csv": parse_chase_csv,
        ".pdf": parse_chase_pdf,
    },
    "amex": {
        ".csv": parse_amex_csv,
        ".pdf": parse_generic_pdf,
    },
    "americanexpress": {
        ".csv": parse_amex_csv,
        ".pdf": parse_generic_pdf,
    },
    "generic": {
        ".csv": parse_generic_csv,
        ".pdf": parse_generic_pdf,
    },
    "capitalone": {
        ".pdf": parse_capital_one_pdf,
    },
    "capital1": {
        ".pdf": parse_capital_one_pdf,
    },
    "cof": {
        ".pdf": parse_capital_one_pdf,
    },
}


def resolve_parser(issuer: str, suffix: str):
    key = (issuer or "generic").lower().replace(" ", "").replace("-", "")
    if key.startswith(("amex", "americanexpress")):
        entry = PARSER_REGISTRY["amex"]
    elif key.startswith("chase"):
        entry = PARSER_REGISTRY["chase"]
    else:
        entry = PARSER_REGISTRY.get(key) or PARSER_REGISTRY["generic"]
    parser = entry.get(suffix.lower())
    if parser is None:
        raise ValueError(f"No parser for issuer={issuer!r} suffix={suffix!r}")
    return parser
