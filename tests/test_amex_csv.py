"""American Express CSV and upload-context regression coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.parsers import resolve_parser
from config.parsers.amex_csv import parse_amex_csv
from src.extract import extract_statements
from src.upload_context import write_upload_context


def _write_export(path: Path) -> None:
    path.write_text(
        "Date,Description,Card Member,Account #,Amount\n"
        "05/25/2026,Interest Charge on Pay Over Time Purchases,ALEX EXAMPLE,,35.31\n"
        "05/24/2026,UBER,ALEX EXAMPLE,,26.95\n"
        "05/21/2026,MOBILE PAYMENT - THANK YOU,SAM EXAMPLE,,-947.69\n"
    )


def test_amex_csv_uses_selected_metadata_and_card_member(tmp_path: Path):
    statement = tmp_path / "amex.csv"
    _write_export(statement)

    parsed = parse_amex_csv(
        statement,
        card="amex-platinum",
        metadata={"card_issuer": "American Express", "card_product": "Platinum"},
    )

    assert parsed["card"].unique().tolist() == ["amex-platinum"]
    assert parsed["card_issuer"].unique().tolist() == ["American Express"]
    assert parsed["card_product"].unique().tolist() == ["Platinum"]
    assert parsed["cardholder"].tolist() == ["Alex Example", "Alex Example", "Sam Example"]
    assert parsed["amount"].tolist() == [35.31, 26.95, -947.69]


def test_amex_csv_rejects_missing_upload_selection(tmp_path: Path):
    statement = tmp_path / "amex.csv"
    _write_export(statement)

    with pytest.raises(ValueError, match="card product"):
        parse_amex_csv(statement, card="amex-platinum", metadata={"card_issuer": "American Express"})


def test_amex_product_folder_key_resolves_to_amex_parser():
    assert resolve_parser("amex-platinum", ".csv") is parse_amex_csv


def test_extraction_passes_persisted_upload_context_to_amex_parser(tmp_path: Path):
    statement = tmp_path / "inbox" / "americanexpress-platinum" / "may.csv"
    statement.parent.mkdir(parents=True)
    _write_export(statement)
    write_upload_context(statement, issuer="American Express", product="Platinum")

    result = extract_statements(statement.parents[1])

    assert len(result.frame) == 3
    assert result.frame["card_product"].unique().tolist() == ["Platinum"]
    assert result.frame["cardholder"].iloc[-1] == "Sam Example"
