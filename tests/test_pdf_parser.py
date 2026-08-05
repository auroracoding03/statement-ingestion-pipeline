"""PDF parser fixtures without depending on an external PDF generator."""

from __future__ import annotations

from pathlib import Path

from config.parsers.generic_pdf import parse_generic_pdf


class _Page:
    def extract_tables(self):
        return [[["01/03/2026", "COFFEE SHOP", "$12.50"]]]

    def extract_text(self):
        return "01/03/2026 COFFEE SHOP $12.50\n01/04/2026 REFUND ($2.25)"


class _Pdf:
    pages = [_Page()]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_generic_pdf_deduplicates_table_and_text_rows(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("config.parsers.generic_pdf.pdfplumber.open", lambda _path: _Pdf())

    parsed = parse_generic_pdf(tmp_path / "statement.pdf", "generic")

    assert len(parsed) == 2
    assert parsed["raw_description"].tolist() == ["COFFEE SHOP", "REFUND"]
    assert parsed["amount"].tolist() == [12.5, -2.25]
