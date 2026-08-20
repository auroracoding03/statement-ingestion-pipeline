"""Synthetic PDF identity detection — no real customer statements."""

from __future__ import annotations

from pathlib import Path

import src.statement_identity as identity


class _Page:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePdf:
    def __init__(self, pages: list[str]):
        self.pages = [_Page(text) for text in pages]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_wells_header_wins_over_chase_merchant_in_body(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "statement.pdf"
    path.write_bytes(b"%PDF-fake")
    pages = [
        "ALEX EXAMPLE\nWELLS FARGO AUTOGRAPH VISA SIGNATURE CARD\nStatement Period 04/08/2026 to 05/08/2026",
        "Trans Date Post Date Description Credits Charges\n05/02 05/02 CHASE CREDIT CRD AUTOPAY 100.00",
    ]
    monkeypatch.setattr(identity.pdfplumber, "open", lambda _path: _FakePdf(pages))

    result = identity.detect_statement_identity(path)

    assert result.issuer == "Wells Fargo"
    assert result.confidence == "detected"
    assert result.needs_manual_details is False
    assert result.needs_cardholder is False
    assert result.account_kind == "card"
    assert "header" in result.message.lower()


def test_chase_merchant_alone_on_foreign_statement_does_not_force_chase(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "mystery.pdf"
    path.write_bytes(b"%PDF-fake")
    pages = [
        "SOME CREDIT UNION VISA SIGNATURE\nAccount summary",
        "Payment to CHASE AUTOMOTIVE FINANCE 45.00",
    ]
    monkeypatch.setattr(identity.pdfplumber, "open", lambda _path: _FakePdf(pages))

    result = identity.detect_statement_identity(path)

    assert result.issuer is None
    assert result.confidence == "manual"


def test_filename_hint_used_when_text_is_ambiguous(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "WellsFargo-Statement.pdf"
    path.write_bytes(b"%PDF-fake")
    # Two strong issuer brands in the same text → ambiguous without filename.
    pages = [
        "Comparison of AMERICAN EXPRESS and WELLS FARGO rewards programs\n"
        "Payment to CHASE AUTOPAY 10.00",
    ]
    monkeypatch.setattr(identity.pdfplumber, "open", lambda _path: _FakePdf(pages))

    result = identity.detect_statement_identity(path)

    assert result.issuer == "Wells Fargo"
    # Wells has a configured product vocabulary, so a bare issuer hit still
    # requires the user to pick Autograph (or another configured product).
    assert result.confidence == "product_required"
    assert result.needs_manual_details is True
    assert "filename" in result.message.lower()


def test_wells_generic_credit_label_requires_product_selection(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "wells-credit.pdf"
    path.write_bytes(b"%PDF-fake")
    pages = [
        "WELLS FARGO CREDIT CARD\nStatement Period 01/08/2024 to 02/07/2024",
        "Trans Date Post Date Description Credits Charges",
    ]
    monkeypatch.setattr(identity.pdfplumber, "open", lambda _path: _FakePdf(pages))

    result = identity.detect_statement_identity(path)

    assert result.issuer == "Wells Fargo"
    assert result.product is None
    assert result.confidence == "product_required"
    assert result.needs_manual_details is True
    assert "select the card product" in result.message.lower()


def test_bank_of_america_without_product_requires_selection(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "boa.pdf"
    path.write_bytes(b"%PDF-fake")
    pages = [
        "ALEX EXAMPLE\nBank of America\nDecember 21 - January 20, 2026",
        "Transaction Date Posting Date Description Amount\n01/17 01/17 PAYMENT THANK YOU -100.00",
    ]
    monkeypatch.setattr(identity.pdfplumber, "open", lambda _path: _FakePdf(pages))

    result = identity.detect_statement_identity(path)

    assert result.issuer == "Bank of America"
    assert result.product is None
    assert result.confidence == "product_required"
    assert result.needs_manual_details is True
    assert "card product" in result.message.lower()


def test_bank_of_america_header_detects_customized_cash_rewards(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "boa.pdf"
    path.write_bytes(b"%PDF-fake")
    pages = [
        "ALEX EXAMPLE\nBank of America Customized Cash Rewards\nDecember 21 - January 20, 2026",
        "Transaction Date Posting Date Description Amount\n01/17 01/17 PAYMENT THANK YOU -100.00",
    ]
    monkeypatch.setattr(identity.pdfplumber, "open", lambda _path: _FakePdf(pages))

    result = identity.detect_statement_identity(path)

    assert result.issuer == "Bank of America"
    assert result.product == "Customized Cash Rewards"
    assert result.confidence == "detected"
    assert result.needs_cardholder is False
    assert result.needs_manual_details is False


def test_chase_header_still_detects_chase(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "card.pdf"
    path.write_bytes(b"%PDF-fake")
    pages = ["ALEX EXAMPLE\nChase Sapphire Preferred\nAccount ending in 1234\nTransaction details"]
    monkeypatch.setattr(identity.pdfplumber, "open", lambda _path: _FakePdf(pages))

    result = identity.detect_statement_identity(path)

    assert result.issuer == "Chase"
    assert result.product == "Sapphire Preferred"
    assert result.confidence == "detected"
    assert result.needs_cardholder is False
    assert result.needs_manual_details is False


def test_amex_csv_without_card_member_names_needs_cardholder(tmp_path: Path) -> None:
    path = tmp_path / "amex.csv"
    path.write_text("Date,Description,Card Member,Account #,Amount\n05/24/2026,UBER,,,26.95\n")

    result = identity.detect_statement_identity(path)

    assert result.issuer == "American Express"
    assert result.confidence == "product_required"
    assert result.needs_cardholder is True
    assert result.needs_manual_details is True
    assert result.account_kind == "card"
    assert "cardholder" in result.message.lower()


def test_amex_csv_with_card_member_names_does_not_need_cardholder(tmp_path: Path) -> None:
    path = tmp_path / "amex.csv"
    path.write_text("Date,Description,Card Member,Account #,Amount\n05/24/2026,UBER,ALEX EXAMPLE,,26.95\n")

    result = identity.detect_statement_identity(path)

    assert result.issuer == "American Express"
    assert result.needs_cardholder is False
    assert result.needs_manual_details is True


def test_pdf_without_name_needs_cardholder(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "card.pdf"
    path.write_bytes(b"%PDF-fake")
    pages = ["Chase Sapphire Preferred\nAccount ending in 1234\nTransaction details"]
    monkeypatch.setattr(identity.pdfplumber, "open", lambda _path: _FakePdf(pages))

    result = identity.detect_statement_identity(path)

    assert result.issuer == "Chase"
    assert result.product == "Sapphire Preferred"
    assert result.confidence == "detected"
    assert result.needs_cardholder is True
    assert result.needs_manual_details is True
