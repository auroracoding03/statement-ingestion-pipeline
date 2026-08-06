"""Native-text Chase PDF parser coverage."""

from __future__ import annotations

import pytest

from config.parsers import resolve_parser
from config.parsers.chase_pdf import _parse_pages, parse_chase_pdf


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


def _header(words: list[dict], top: float) -> None:
    _line(words, top, [(50, "Transaction"), (220, "Merchant Name or Transaction Description"), (500, "$ Amount")])


def _row(words: list[dict], top: float, posted: str, description: str, amount: str) -> None:
    _line(words, top, [(50, posted), (220, description), (500, amount)])


def _amazon_page() -> _Page:
    words: list[dict] = []
    _line(words, 10, [(50, "amazon | CHASE"), (250, "YOUR PRIME VISA POINTS")])
    _line(words, 15, [(50, "Opening/Closing Date"), (250, "04/25/26 - 05/24/26")])
    _line(words, 20, [(50, "ALEX EXAMPLE")])
    _line(words, 30, [(50, "PAYMENTS AND OTHER CREDITS")])
    _header(words, 35)
    _row(words, 40, "04/27", "Payment Thank You - Web", "-1,049.15")
    _line(words, 45, [(50, "PURCHASE")])
    _header(words, 50)
    _row(words, 55, "05/08", "AMAZON MKTPL*BF1GH4BC2 Amzn.com/bill WA", "58.29")
    _line(words, 60, [(220, "Order Number 111-8573359-6501034")])
    _line(words, 65, [(50, "PURCHASES AND REDEMPTIONS")])
    _header(words, 70)
    _row(words, 75, "05/10", "AMAZON.COM AMZN.COM/BILL WA", "12.71")
    return _Page(words)


def test_chase_amazon_parser_extracts_activity_and_metadata():
    parsed = _parse_pages([_amazon_page()], card="chase-amazon", source_file="chase/may.pdf")

    assert len(parsed) == 2
    assert parsed["card_issuer"].unique().tolist() == ["Chase"]
    assert parsed["card_product"].unique().tolist() == ["Amazon Prime Visa"]
    assert parsed["cardholder"].unique().tolist() == ["Alex Example"]
    assert parsed["amount"].tolist() == [-1049.15, 58.29]
    assert parsed["posted_date"].astype(str).tolist() == ["2026-04-27", "2026-05-08"]
    assert parsed["raw_description"].iloc[1].endswith("Order Number 111-8573359-6501034")


def test_chase_sapphire_product_and_holder_are_detected():
    words: list[dict] = []
    _line(words, 10, [(50, "CHASE SAPPHIRE PREFERRED")])
    _line(words, 15, [(50, "01/20/26 - 02/19/26")])
    _line(words, 20, [(50, "SAM EXAMPLE")])
    _line(words, 30, [(50, "PURCHASE")])
    _header(words, 35)
    _row(words, 40, "02/01", "COFFEE SHOP", "6.50")

    parsed = _parse_pages([_Page(words)], card="chase-sapphire", source_file="chase/feb.pdf")

    assert parsed.loc[0, "card_product"] == "Sapphire Preferred"
    assert parsed.loc[0, "cardholder"] == "Sam Example"


def test_chase_parser_rejects_image_only_statement():
    with pytest.raises(ValueError, match="no extractable text"):
        _parse_pages([_Page([])], card="chase", source_file="chase/image.pdf")


def test_chase_product_key_routes_to_dedicated_parser():
    assert resolve_parser("chase-sapphire", ".pdf") is parse_chase_pdf
