"""Capital One PDF parsing and statement-metadata regression coverage."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from config.parsers import resolve_parser
from config.parsers.capital_one_pdf import _parse_pages, parse_capital_one_pdf


class _Page:
    def __init__(self, words: list[dict]):
        self._words = words

    def extract_words(self):
        return self._words


def _add_line(words: list[dict], y: float, cells: list[tuple[float, str]]) -> None:
    for x, value in cells:
        cursor = x
        for token in value.split():
            words.append({"text": token, "x0": cursor, "top": y})
            cursor += len(token) * 5.8 + 4


def _header(words: list[dict], y: float) -> None:
    _add_line(
        words,
        y,
        [(50, "Trans Date"), (155, "Post Date"), (280, "Description"), (530, "Amount")],
    )


def _activity_row(
    words: list[dict], y: float, trans_date: str, post_date: str, description: str, amount: str
) -> None:
    _add_line(words, y, [(50, trans_date), (155, post_date), (280, description), (530, amount)])


def _capital_one_pages() -> list[_Page]:
    words: list[dict] = []
    _add_line(
        words,
        10,
        [
            (50, "Capital One"),
            (180, "Savor Credit Card | World Elite Mastercard"),
            (430, "Jun 19, 2025 - Jul 19, 2025"),
        ],
    )
    for y, label, value in [
        (25, "Previous Balance", "$52.08"),
        (30, "Payments", "-$406.85"),
        (35, "Other Credits", "$0.00"),
        (40, "Transactions", "+$423.09"),
        (45, "Cash Advances", "+$0.00"),
        (50, "Fees Charged", "+$25.00"),
        (55, "Interest Charged", "+$3.56"),
        (60, "New Balance", "$96.88"),
    ]:
        _add_line(words, y, [(50, label), (530, value)])

    _add_line(words, 80, [(50, "Alex Example : Payments, Credits and Adjustments")])
    _header(words, 85)
    _activity_row(words, 90, "Jul 15", "Jul 15", "CAPITAL ONE MOBILE PYMT", "-$406.85")

    _add_line(words, 105, [(50, "Alex Example : Transactions")])
    _header(words, 110)
    purchases = [
        ("Jun 25", "Jun 26", "FIVE GUYS 0174 ECOMMSMYRNAGA", "$27.63"),
        ("Jul 1", "Jul 2", "PP *Rileys Meadow OwneGreensboroNC", "$192.05"),
        ("Jul 2", "Jul 3", "Roosters MGCAtlantaGA", "$50.00"),
        ("Jul 2", "Jul 3", "LOS ABUELOS MARIETTAMARIETTAGA", "$58.18"),
        ("Jul 2", "Jul 3", "SCRUBHUB CAR WASHSMYRNAGA", "$13.00"),
        ("Jul 3", "Jul 4", "WALGREENS #19823ATLANTAGA", "$13.91"),
        ("Jul 13", "Jul 14", "WHISTLEEXPRESSCARWASHCHARLOTTENC", "$10.00"),
        ("Jul 13", "Jul 14", "SMALL CAKESMARIETTAGA", "$18.60"),
        ("Jul 17", "Jul 19", "CHICK-FIL-A #00802MARIETTAGA", "$3.79"),
        ("Jul 18", "Jul 19", "CITY OF GRAHAM FEEGRAHAMNC", "$1.03"),
        ("Jul 18", "Jul 19", "CITY OF GRAHAMGRAHAMNC", "$34.90"),
    ]
    for offset, row in enumerate(purchases, start=1):
        _activity_row(words, 110 + offset * 5, *row)
    _add_line(words, 170, [(50, "Total Transactions"), (530, "$423.09")])

    _add_line(words, 180, [(50, "Sam Example : Fees")])
    _header(words, 185)
    _activity_row(words, 190, "Jul 14", "Jul 14", "PAST DUE FEE", "$25.00")
    _add_line(words, 195, [(50, "Total Fees for This Period"), (530, "$25.00")])

    _add_line(words, 205, [(50, "Sam Example : Interest Charged")])
    _header(words, 210)
    _add_line(words, 215, [(280, "Interest Charge on Purchases"), (530, "$3.56")])
    _add_line(words, 220, [(280, "Interest Charge on Cash Advances"), (530, "$0.00")])
    _add_line(words, 225, [(50, "Total Interest for This Period"), (530, "$3.56")])
    _add_line(words, 230, [(50, "Totals Year-to-Date"), (530, "$28.56")])
    return [_Page(words)]


def test_capital_one_parser_extracts_activity_and_statement_metadata():
    parsed = _parse_pages(_capital_one_pages(), card="capitalone", source_file="capitalone/july.pdf")

    assert len(parsed) == 14
    assert parsed["card_issuer"].unique().tolist() == ["Capital One"]
    assert parsed["card_product"].unique().tolist() == ["Savor"]
    assert set(parsed["cardholder"].dropna()) == {"Alex Example", "Sam Example"}

    purchases = parsed[parsed["amount"] > 0]
    assert purchases.loc[purchases["raw_description"] == "FIVE GUYS 0174 ECOMMSMYRNAGA", "posted_date"].iloc[0].isoformat() == "2025-06-26"
    assert purchases["amount"].sum() == pytest.approx(451.65)
    assert parsed.loc[parsed["raw_description"] == "CAPITAL ONE MOBILE PYMT", "amount"].iloc[0] == -406.85
    assert parsed.loc[parsed["raw_description"] == "Interest Charge on Purchases", "posted_date"].iloc[0].isoformat() == "2025-07-19"
    assert not parsed["raw_description"].str.contains("Total|Year-to-Date", regex=True).any()

    transaction_rows = parsed[~parsed["raw_description"].isin(["PAST DUE FEE", "Interest Charge on Purchases", "CAPITAL ONE MOBILE PYMT"])]
    assert len(transaction_rows) == 11
    assert transaction_rows["amount"].sum() == pytest.approx(423.09)


def test_capital_one_parser_keeps_dates_within_one_day_grace():
    words: list[dict] = []
    _add_line(
        words,
        10,
        [
            (50, "Capital One"),
            (180, "Savor Credit Card | World Elite Mastercard"),
            (430, "Nov 08, 2024 - Dec 08, 2024"),
        ],
    )
    _add_line(words, 20, [(50, "Alex Example : Transactions")])
    _header(words, 25)
    _activity_row(words, 30, "Nov 07", "Nov 07", "EDGE DATE MERCHANT", "$9.99")
    _activity_row(words, 35, "Nov 20", "Nov 20", "IN PERIOD MERCHANT", "$4.50")
    _add_line(words, 40, [(50, "Total Transactions"), (530, "$14.49")])

    parsed = _parse_pages([_Page(words)], card="capitalone", source_file="capitalone/grace.pdf")

    assert parsed["posted_date"].astype(str).tolist() == ["2024-11-07", "2024-11-20"]
    assert parsed["amount"].tolist() == [9.99, 4.50]


def test_capital_one_parser_ignores_non_money_tokens_in_amount_column():
    words: list[dict] = []
    _add_line(
        words,
        10,
        [
            (50, "Capital One"),
            (180, "Savor Credit Card | World Elite Mastercard"),
            (430, "Dec 08, 2025 - Jan 08, 2026"),
        ],
    )
    _add_line(words, 20, [(50, "Alex Example : Transactions")])
    _header(words, 25)
    _activity_row(words, 30, "Dec 13", "Dec 13", "DUMPLING SHOP LONDON", "$36.86")
    _add_line(words, 30, [(600, "WC2H")])
    _add_line(words, 35, [(50, "Total Transactions"), (530, "$36.86")])

    parsed = _parse_pages([_Page(words)], card="capitalone", source_file="capitalone/overflow.pdf")

    assert parsed["amount"].tolist() == [36.86]


def test_capital_one_parser_skips_dates_far_outside_period():
    words: list[dict] = []
    _add_line(
        words,
        10,
        [
            (50, "Capital One"),
            (180, "Savor Credit Card | World Elite Mastercard"),
            (430, "Nov 08, 2024 - Dec 08, 2024"),
        ],
    )
    _add_line(words, 20, [(50, "Alex Example : Transactions")])
    _header(words, 25)
    _activity_row(words, 30, "Oct 01", "Oct 01", "STALE MERCHANT", "$50.00")
    _activity_row(words, 35, "Nov 20", "Nov 20", "IN PERIOD MERCHANT", "$4.50")
    _add_line(words, 40, [(50, "Total Transactions"), (530, "$4.50")])

    parsed = _parse_pages([_Page(words)], card="capitalone", source_file="capitalone/outside.pdf")

    assert len(parsed) == 1
    assert parsed["posted_date"].astype(str).tolist() == ["2024-11-20"]
    assert parsed["amount"].tolist() == [4.50]


def test_capital_one_parser_keeps_leap_day_in_leap_year_cycle():
    words: list[dict] = []
    _add_line(
        words,
        10,
        [
            (50, "Capital One"),
            (180, "Savor Credit Card | World Elite Mastercard"),
            (430, "Feb 25, 2024 - Mar 24, 2024"),
        ],
    )
    _add_line(words, 20, [(50, "Alex Example : Transactions")])
    _header(words, 25)
    _activity_row(words, 30, "Feb 29", "Feb 29", "LEAP DAY MERCHANT", "$6.50")
    _activity_row(words, 35, "Mar 01", "Mar 01", "IN PERIOD MERCHANT", "$4.25")
    _add_line(words, 40, [(50, "Total Transactions"), (530, "$10.75")])

    parsed = _parse_pages([_Page(words)], card="capitalone", source_file="capitalone/leap.pdf")

    assert parsed["posted_date"].astype(str).tolist() == ["2024-02-29", "2024-03-01"]


def test_capital_one_parser_rejects_image_only_documents():
    with pytest.raises(ValueError, match="no extractable text"):
        _parse_pages([_Page([])], card="capitalone", source_file="capitalone/image.pdf")


def test_capital_one_parser_requires_known_layout():
    words: list[dict] = []
    _add_line(words, 10, [(50, "Capital One Savor Credit Card | World Elite Mastercard")])
    _add_line(words, 20, [(50, "Jun 19, 2025 - Jul 19, 2025")])
    with pytest.raises(ValueError, match="transaction table was not found"):
        _parse_pages([_Page(words)], card="capitalone", source_file="capitalone/unknown.pdf")


def test_capital_one_registry_aliases_resolve_to_dedicated_parser():
    for alias in ("capitalone", "capital1", "cof"):
        assert resolve_parser(alias, ".pdf") is parse_capital_one_pdf


def test_normalization_preserves_statement_metadata():
    from src.normalize import normalize

    ledger = normalize(
        pd.DataFrame(
            [
                {
                    "posted_date": "2025-07-19",
                    "amount": 3.56,
                    "raw_description": "INTEREST CHARGE ON PURCHASES",
                    "card": "capitalone",
                    "card_issuer": "Capital One",
                    "card_product": "Savor",
                    "cardholder": "Alex Example",
                    "source_file": "capitalone/july.pdf",
                }
            ]
        )
    )
    assert ledger.loc[0, "card_issuer"] == "Capital One"
    assert ledger.loc[0, "card_product"] == "Savor"
    assert ledger.loc[0, "cardholder"] == "Alex Example"
