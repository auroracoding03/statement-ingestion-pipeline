"""Native-text Bank of America credit-card PDF parser coverage."""

from __future__ import annotations

from pathlib import Path
import json

import pandas as pd
import pytest

import src.paths as paths
from config.parsers import resolve_parser
from config.parsers.bank_of_america import (
    _parse_pages,
    parse_bank_of_america_csv,
    parse_bank_of_america_pdf,
)


class _Page:
    def __init__(self, words: list[dict]):
        self.words = words

    def extract_words(self):
        return self.words


def _line(words: list[dict], top: float, cells: list[tuple[float, str]]) -> None:
    for x, text in cells:
        cursor = x
        for token in text.split():
            words.append({"text": token, "x0": cursor, "top": top})
            cursor += len(token) * 5.8 + 4


def _stacked_header(words: list[dict], top: float) -> None:
    _line(
        words,
        top,
        [
            (40, "Transaction"),
            (140, "Posting"),
            (430, "Reference"),
            (530, "Account"),
        ],
    )
    _line(
        words,
        top + 3,
        [
            (40, "Date"),
            (140, "Date"),
            (230, "Description"),
            (430, "Number"),
            (530, "Number"),
            (620, "Amount"),
            (700, "Total"),
        ],
    )


def _header(words: list[dict], top: float) -> None:
    _line(
        words,
        top,
        [
            (40, "Transaction Date"),
            (140, "Posting Date"),
            (230, "Description"),
            (430, "Reference Number"),
            (530, "Account Number"),
            (620, "Amount"),
            (700, "Total"),
        ],
    )


def _row(
    words: list[dict],
    top: float,
    trans: str,
    post: str,
    description: str,
    amount: str,
    *,
    reference: str = "5365",
    account: str = "4794",
    total: str = "",
) -> None:
    cells = [
        (40, trans),
        (140, post),
        (230, description),
        (430, reference),
        (530, account),
        (620, amount),
    ]
    if total:
        cells.append((700, total))
    _line(words, top, cells)


def _identity(words: list[dict], *, product: str = "Customized Cash Rewards") -> None:
    _line(words, 8, [(40, "Bank of America")])
    _line(words, 12, [(40, product)])
    _line(words, 16, [(40, "December 21 - January 20, 2026")])
    _line(words, 20, [(40, "ALEX EXAMPLE")])


def _statement_pages() -> list[_Page]:
    words: list[dict] = []
    _identity(words)
    _line(words, 28, [(40, "Previous Balance"), (620, "$0.00")])
    _line(words, 32, [(40, "Payments and Other Credits"), (620, "-$695.81")])
    _line(words, 36, [(40, "Purchases and Adjustments"), (620, "$709.20")])
    _line(words, 40, [(40, "Fees Charged"), (620, "$0.00")])
    _line(words, 44, [(40, "Interest Charged"), (620, "$0.00")])
    _line(words, 48, [(40, "New Balance Total"), (620, "$13.39")])

    _line(words, 60, [(40, "Transactions")])
    _header(words, 65)
    _line(words, 70, [(40, "Payments and Other Credits")])
    _row(words, 75, "01/17", "01/17", "PAYMENT FROM CHK 1702 CONF#4e94w5q9t", "-695.81")
    _line(words, 80, [(230, "TOTAL PAYMENTS AND OTHER CREDITS FOR THIS PERIOD"), (700, "-$695.81")])

    _line(words, 90, [(40, "Purchases and Adjustments")])
    purchases = [
        ("12/24", "12/26", "Google One 650-2530000 CA", "9.99"),
        ("12/26", "12/26", "Spotify P3DCB13857 New York NY", "11.99"),
        ("01/02", "01/03", "ATT * BILL PAYMENT KH4589@ATT.CCTX", "364.46"),
        ("01/15", "01/16", "STUBHUB INC 866-788-2482 CA", "209.37"),
        ("01/16", "01/17", "INK CARDS POSTAGRAN 877-248-8906 CA", "21.06"),
        ("01/18", "01/19", "POSHMARK 650-488-7740 CA", "92.33"),
    ]
    for offset, row in enumerate(purchases, start=1):
        _row(words, 90 + offset * 5, *row)
    _line(words, 125, [(230, "TOTAL PURCHASES AND ADJUSTMENTS FOR THIS PERIOD"), (700, "$709.20")])

    _line(words, 135, [(40, "Interest Charged")])
    _row(words, 140, "01/20", "01/20", "Interest Charge on Purchases", "0.00")
    _row(words, 145, "01/20", "01/20", "Interest Charge on Balance Transfers", "12.50")
    _line(words, 150, [(230, "TOTAL INTEREST CHARGED FOR THIS PERIOD"), (700, "$12.50")])
    _line(words, 155, [(40, "2026 Totals Year-to-Date"), (620, "$0.00")])
    _line(words, 165, [(40, "Interest Charge Calculation")])
    _line(words, 170, [(40, "Purchases"), (620, "$0.00")])
    return [_Page(words)]


def test_bank_of_america_parser_extracts_activity_and_metadata():
    parsed = _parse_pages(_statement_pages(), card="boa-cash", source_file="boa/jan.pdf")

    assert parsed["card_issuer"].unique().tolist() == ["Bank of America"]
    assert parsed["card_product"].unique().tolist() == ["Customized Cash Rewards"]
    assert parsed["cardholder"].unique().tolist() == ["Alex Example"]
    assert parsed.loc[parsed["raw_description"].str.startswith("PAYMENT"), "amount"].iloc[0] == -695.81
    assert parsed.loc[parsed["raw_description"] == "Google One 650-2530000 CA", "posted_date"].iloc[0].isoformat() == "2025-12-26"
    assert parsed.loc[parsed["raw_description"] == "Interest Charge on Balance Transfers", "amount"].iloc[0] == 12.50
    assert parsed.loc[parsed["raw_description"] == "Interest Charge on Balance Transfers", "posted_date"].iloc[0].isoformat() == "2026-01-20"
    purchases = parsed[parsed["amount"] > 0]
    assert purchases["amount"].sum() == pytest.approx(721.70)
    assert not parsed["raw_description"].str.contains("TOTAL|Year-to-Date|Interest Charge Calculation", regex=True).any()
    assert "Interest Charge on Purchases" not in set(parsed["raw_description"])
    assert not parsed["raw_description"].str.contains("5365|4794", regex=True).any()
    log_path = paths.LOGS_DIR / "boa-parser.ndjson"
    assert log_path.exists()
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {event["message"] for event in events} >= {"parse start sidecar", "identity scan", "header candidates", "table scan result"}
    assert any(event["data"].get("file") == "jan.pdf" and event["data"].get("found_table") is True for event in events)


def test_bank_of_america_parser_uses_upload_product_when_statement_omits_it():
    words: list[dict] = []
    _line(words, 8, [(40, "Bank of America")])
    _line(words, 12, [(40, "December 21 - January 20, 2026")])
    _line(words, 16, [(40, "ALEX EXAMPLE")])
    _line(words, 30, [(40, "Transactions")])
    _header(words, 35)
    _line(words, 40, [(40, "Purchases and Adjustments")])
    _row(words, 45, "01/05", "01/06", "COFFEE SHOP", "4.50")

    parsed = _parse_pages(
        [_Page(words)],
        card="boa-air-france",
        source_file="boa/air-france.pdf",
        upload_metadata={"card_product": "Air France"},
    )
    assert parsed["card_product"].unique().tolist() == ["Air France"]
    assert parsed["amount"].tolist() == [4.50]


def test_bank_of_america_parser_reads_two_line_transaction_header():
    words: list[dict] = []
    _identity(words)
    _line(words, 28, [(40, "Transactions")])
    _stacked_header(words, 32)
    _line(words, 40, [(40, "Purchases and Adjustments")])
    _row(words, 45, "01/05", "01/06", "COFFEE SHOP", "4.50")

    parsed = _parse_pages([_Page(words)], card="boa", source_file="boa/stacked.pdf")
    assert parsed["amount"].tolist() == [4.50]
    assert parsed["raw_description"].tolist() == ["COFFEE SHOP"]
    log_path = paths.LOGS_DIR / "boa-parser.ndjson"
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(
        event["data"].get("file") == "stacked.pdf"
        and event["data"].get("found_table") is True
        and event["data"].get("header_mode") == "stacked"
        for event in events
    )


def test_bank_of_america_parser_keeps_dates_within_one_day_grace():
    words: list[dict] = []
    _identity(words)
    _header(words, 30)
    _line(words, 35, [(40, "Purchases and Adjustments")])
    _row(words, 40, "12/20", "12/20", "EDGE DATE MERCHANT", "9.99")
    _row(words, 45, "01/10", "01/10", "IN PERIOD MERCHANT", "4.50")

    parsed = _parse_pages([_Page(words)], card="boa", source_file="boa/grace.pdf")
    assert parsed["posted_date"].astype(str).tolist() == ["2025-12-20", "2026-01-10"]


def test_bank_of_america_parser_skips_dates_far_outside_period():
    words: list[dict] = []
    _identity(words)
    _header(words, 30)
    _line(words, 35, [(40, "Purchases and Adjustments")])
    _row(words, 40, "10/01", "10/01", "STALE MERCHANT", "50.00")
    _row(words, 45, "01/10", "01/10", "IN PERIOD MERCHANT", "4.50")

    parsed = _parse_pages([_Page(words)], card="boa", source_file="boa/outside.pdf")
    assert parsed["posted_date"].astype(str).tolist() == ["2026-01-10"]
    assert parsed["amount"].tolist() == [4.50]


def test_bank_of_america_parser_keeps_leap_day_in_leap_year_cycle():
    words: list[dict] = []
    _line(words, 8, [(40, "Bank of America")])
    _line(words, 12, [(40, "Customized Cash Rewards")])
    _line(words, 16, [(40, "February 25, 2024 to March 24, 2024")])
    _line(words, 20, [(40, "ALEX EXAMPLE")])
    _header(words, 30)
    _line(words, 35, [(40, "Purchases and Adjustments")])
    _row(words, 40, "02/29", "02/29", "LEAP DAY MERCHANT", "6.50")
    _row(words, 45, "03/01", "03/01", "IN PERIOD MERCHANT", "4.25")

    parsed = _parse_pages([_Page(words)], card="boa", source_file="boa/leap.pdf")
    assert parsed["posted_date"].astype(str).tolist() == ["2024-02-29", "2024-03-01"]


def test_bank_of_america_parser_rejects_image_only_statement():
    with pytest.raises(ValueError, match="no extractable text"):
        _parse_pages([_Page([])], card="boa", source_file="boa/image.pdf")


def test_bank_of_america_parser_requires_known_layout():
    words: list[dict] = []
    _identity(words)
    with pytest.raises(ValueError, match="transaction table was not found"):
        _parse_pages([_Page(words)], card="boa", source_file="boa/unknown.pdf")


def test_bank_of_america_registry_aliases_route_to_pdf_parser():
    for key in ("bankofamerica", "bankofamerica-regular", "bankofamerica-air-france", "boa", "boa-air-france"):
        assert resolve_parser(key, ".pdf") is parse_bank_of_america_pdf
        assert resolve_parser(key, ".csv") is parse_bank_of_america_csv


def test_bank_of_america_csv_is_pdf_only(tmp_path: Path):
    parser = resolve_parser("boa", ".csv")
    with pytest.raises(ValueError, match="PDF-only"):
        parser(tmp_path / "export.csv", "boa", {"card_product": "Air France"})


def test_normalization_preserves_bank_of_america_metadata():
    from src.normalize import normalize

    ledger = normalize(
        pd.DataFrame(
            [
                {
                    "posted_date": "2026-01-17",
                    "amount": -695.81,
                    "raw_description": "PAYMENT FROM CHK 1702",
                    "card": "boa-cash",
                    "card_issuer": "Bank of America",
                    "card_product": "Customized Cash Rewards",
                    "cardholder": "Alex Example",
                    "source_file": "boa/jan.pdf",
                }
            ]
        )
    )
    assert ledger.loc[0, "card_issuer"] == "Bank of America"
    assert ledger.loc[0, "card_product"] == "Customized Cash Rewards"
    assert ledger.loc[0, "cardholder"] == "Alex Example"
