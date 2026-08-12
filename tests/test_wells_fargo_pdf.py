"""Native-text Wells Fargo parsing and Bank of America placeholders."""

from __future__ import annotations

import pytest

from config.parsers import resolve_parser
from config.parsers.bank_of_america import parse_bank_of_america_placeholder
from config.parsers.wells_fargo_pdf import _parse_pages, parse_wells_fargo_pdf


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
    _line(
        words,
        top,
        [
            (75, "Card Ending in"),
            (150, "Trans Date"),
            (205, "Post Date"),
            (275, "Reference Number"),
            (445, "Description"),
            (905, "Credits"),
            (1005, "Charges"),
        ],
    )


def _row(words: list[dict], top: float, trans: str, post: str, reference: str, description: str, credit: str = "", charge: str = "") -> None:
    cells = [(75, "1234"), (150, trans), (205, post), (275, reference), (445, description)]
    if credit:
        cells.append((905, credit))
    if charge:
        cells.append((1005, charge))
    _line(words, top, cells)


def _statement_page() -> _Page:
    words: list[dict] = []
    _line(words, 10, [(75, "WELLS FARGO AUTOGRAPH VISA SIGNATURE® CARD")])
    _line(words, 15, [(75, "Statement Period 04/08/2026 to 05/08/2026")])
    _line(words, 20, [(75, "ALEX N EXAMPLE")])
    _line(words, 30, [(75, "Transactions")])
    _header(words, 35)
    _line(words, 40, [(75, "Payments")])
    _row(words, 45, "04/30", "04/30", "X123", "ONLINE PAYMENT THANK YOU", credit="316.72")
    _line(words, 50, [(75, "Purchases, Balance Transfers & Other Charges")])
    _row(words, 55, "05/02", "05/02", "ABC123", "GOOGLE *YouTubePremium g.co/helppay# CA", charge="13.99")
    _row(words, 60, "05/06", "05/06", "ABC124", "COMCAST / XFINITY 800-266-2278 GA", charge="80.00")
    _line(words, 65, [(75, "Fees Charged")])
    _line(words, 70, [(75, "Interest Charged")])
    return _Page(words)


def test_wells_fargo_parser_extracts_signs_product_and_holder():
    parsed = _parse_pages([_statement_page()], card="wellsfargo-autograph", source_file="wells/may.pdf")

    assert parsed["card_issuer"].unique().tolist() == ["Wells Fargo"]
    assert parsed["card_product"].unique().tolist() == ["Autograph Visa Signature"]
    assert parsed["cardholder"].unique().tolist() == ["Alex Example"]
    assert parsed["amount"].tolist() == [-316.72, 13.99, 80.0]
    assert parsed["posted_date"].astype(str).tolist() == ["2026-04-30", "2026-05-02", "2026-05-06"]


def test_wells_fargo_parser_keeps_charge_when_description_overflows_credits():
    words: list[dict] = []
    _line(words, 10, [(75, "WELLS FARGO AUTOGRAPH VISA SIGNATURE® CARD")])
    _line(words, 15, [(75, "Statement Period 12/08/2025 to 03/08/2026")])
    _line(words, 20, [(75, "ALEX N EXAMPLE")])
    _line(words, 30, [(75, "Transactions")])
    _header(words, 35)
    _line(words, 40, [(75, "Purchases, Balance Transfers & Other Charges")])
    _row(words, 45, "12/13", "12/13", "ABC123", "DUMPLING SHOP LONDON", charge="36.86")
    _line(words, 45, [(700, "WC2H GB")])
    _row(words, 50, "02/23", "02/23", "ABC124", "LAST STATEMENT BAL FROM ACCT", charge="464.02")
    _line(words, 50, [(700, "ENDING 7350")])

    parsed = _parse_pages([_Page(words)], card="wellsfargo-autograph", source_file="wells/overflow.pdf")

    assert parsed["amount"].tolist() == [36.86, 464.02]
    assert "WC2H GB" in parsed["raw_description"].iloc[0]
    assert "ENDING 7350" in parsed["raw_description"].iloc[1]


def test_wells_fargo_parser_prefers_upload_product_over_generic_credit_label():
    words: list[dict] = []
    _line(words, 10, [(75, "WELLS FARGO CREDIT CARD")])
    _line(words, 15, [(75, "Statement Period 04/08/2026 to 05/08/2026")])
    _line(words, 20, [(75, "ALEX N EXAMPLE")])
    _line(words, 30, [(75, "Transactions")])
    _header(words, 35)
    _line(words, 40, [(75, "Purchases, Balance Transfers & Other Charges")])
    _row(words, 45, "05/02", "05/02", "ABC123", "COFFEE SHOP ATLANTA GA", charge="4.50")

    parsed = _parse_pages(
        [_Page(words)],
        card="wellsfargo-autograph",
        source_file="wells/credit.pdf",
        upload_metadata={"card_issuer": "Wells Fargo", "card_product": "Autograph Visa Signature"},
    )
    assert parsed["card_product"].unique().tolist() == ["Autograph Visa Signature"]


def test_wells_fargo_parser_keeps_dates_within_one_day_grace():
    words: list[dict] = []
    _line(words, 10, [(75, "WELLS FARGO AUTOGRAPH VISA SIGNATURE® CARD")])
    _line(words, 15, [(75, "Statement Period 11/08/2024 to 12/08/2024")])
    _line(words, 20, [(75, "ALEX N EXAMPLE")])
    _line(words, 30, [(75, "Transactions")])
    _header(words, 35)
    _line(words, 40, [(75, "Purchases, Balance Transfers & Other Charges")])
    _row(words, 45, "11/07", "11/07", "ABC100", "EDGE DATE MERCHANT GA", charge="9.99")
    _row(words, 50, "11/20", "11/20", "ABC101", "IN PERIOD MERCHANT GA", charge="4.50")

    parsed = _parse_pages([_Page(words)], card="wellsfargo-autograph", source_file="wells/grace.pdf")

    assert parsed["posted_date"].astype(str).tolist() == ["2024-11-07", "2024-11-20"]
    assert parsed["amount"].tolist() == [9.99, 4.50]


def test_wells_fargo_parser_skips_dates_far_outside_period():
    words: list[dict] = []
    _line(words, 10, [(75, "WELLS FARGO AUTOGRAPH VISA SIGNATURE® CARD")])
    _line(words, 15, [(75, "Statement Period 11/08/2024 to 12/08/2024")])
    _line(words, 20, [(75, "ALEX N EXAMPLE")])
    _line(words, 30, [(75, "Transactions")])
    _header(words, 35)
    _line(words, 40, [(75, "Purchases, Balance Transfers & Other Charges")])
    _row(words, 45, "10/01", "10/01", "ABC100", "STALE MERCHANT GA", charge="50.00")
    _row(words, 50, "11/20", "11/20", "ABC101", "IN PERIOD MERCHANT GA", charge="4.50")

    parsed = _parse_pages([_Page(words)], card="wellsfargo-autograph", source_file="wells/outside.pdf")

    assert len(parsed) == 1
    assert parsed["posted_date"].astype(str).tolist() == ["2024-11-20"]
    assert parsed["amount"].tolist() == [4.50]


def test_wells_fargo_parser_keeps_leap_day_in_leap_year_cycle():
    words: list[dict] = []
    _line(words, 10, [(75, "WELLS FARGO AUTOGRAPH VISA SIGNATURE® CARD")])
    _line(words, 15, [(75, "Statement Period 02/25/2024 to 03/24/2024")])
    _line(words, 20, [(75, "ALEX N EXAMPLE")])
    _line(words, 30, [(75, "Transactions")])
    _header(words, 35)
    _line(words, 40, [(75, "Purchases, Balance Transfers & Other Charges")])
    _row(words, 45, "02/29", "02/29", "ABC100", "LEAP DAY MERCHANT GA", charge="6.50")
    _row(words, 50, "03/01", "03/01", "ABC101", "IN PERIOD MERCHANT GA", charge="4.25")

    parsed = _parse_pages([_Page(words)], card="wellsfargo-autograph", source_file="wells/leap.pdf")

    assert parsed["posted_date"].astype(str).tolist() == ["2024-02-29", "2024-03-01"]


def test_wells_fargo_parser_rejects_image_only_statement():
    with pytest.raises(ValueError, match="no extractable text"):
        _parse_pages([_Page([])], card="wellsfargo", source_file="wells/image.pdf")


def test_wells_fargo_registry_aliases_route_to_dedicated_parser():
    for key in ("wellsfargo", "wellsfargo-autograph", "wf"):
        assert resolve_parser(key, ".pdf") is parse_wells_fargo_pdf


def test_bank_of_america_regular_and_air_france_route_to_safe_placeholder(tmp_path):
    for key in ("bankofamerica-regular", "bankofamerica-air-france", "boa-air-france"):
        parser = resolve_parser(key, ".pdf")
        assert parser is parse_bank_of_america_placeholder
        with pytest.raises(ValueError, match="placeholder"):
            parser(tmp_path / "statement.pdf", key, {"card_product": "Air France"})
