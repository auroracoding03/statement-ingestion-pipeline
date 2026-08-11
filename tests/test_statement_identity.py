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
        "WELLS FARGO AUTOGRAPH VISA SIGNATURE CARD\nStatement Period 04/08/2026 to 05/08/2026",
        "Trans Date Post Date Description Credits Charges\n05/02 05/02 CHASE CREDIT CRD AUTOPAY 100.00",
    ]
    monkeypatch.setattr(identity.pdfplumber, "open", lambda _path: _FakePdf(pages))

    result = identity.detect_statement_identity(path)

    assert result.issuer == "Wells Fargo"
    assert result.confidence == "detected"
    assert result.needs_manual_details is False
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
    assert result.confidence == "detected"
    assert "filename" in result.message.lower()


def test_chase_header_still_detects_chase(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "card.pdf"
    path.write_bytes(b"%PDF-fake")
    pages = ["Chase Sapphire Preferred\nAccount ending in 1234\nTransaction details"]
    monkeypatch.setattr(identity.pdfplumber, "open", lambda _path: _FakePdf(pages))

    result = identity.detect_statement_identity(path)

    assert result.issuer == "Chase"
    assert result.product == "Sapphire Preferred"
    assert result.confidence == "detected"
