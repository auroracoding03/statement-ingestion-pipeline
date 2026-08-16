"""Wells Fargo account-history CSV parser and identity."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from config.parsers import resolve_parser
from config.parsers.wells_fargo_csv import parse_wells_fargo_csv
from config.parsers.wells_fargo_pdf import parse_wells_fargo_pdf
from src.extract import extract_statements
from src.normalize import normalize
from src.statement_identity import detect_statement_identity
from src.upload_context import write_upload_context


def _write_history(path: Path, extra: str = "") -> None:
    path.write_text(
        '"DATE","DESCRIPTION","AMOUNT","CHECK #","STATUS"\n'
        '"08/04/2026","BILL PAY Example HOA, Inc ON-LINE","-388.00","","Posted"\n'
        '"08/14/2026","ACME CORP PAYROLL XXXXX1234 Alex Example","4692.05","","Posted"\n'
        '"08/05/2026","AMERICAN EXPRESS ACH PMT 260101 W9999 Alex Example","-2631.60","","Posted"\n'
        '"08/03/2026","ONLINE TRANSFER TO ALEX A EVERYDAY CHECKING XXXXXX9999","-700.00","","Posted"\n'
        '"07/31/2026","INTEREST PAYMENT","0.05","","Posted"\n'
        '"08/10/2026","VENMO PAYMENT PENDING","-10.00","","Pending"\n'
        + extra,
        encoding="utf-8",
    )


def test_wells_fargo_csv_flips_signs_and_skips_pending(tmp_path: Path) -> None:
    statement = tmp_path / "checking.csv"
    _write_history(statement)
    parsed = parse_wells_fargo_csv(
        statement,
        card="wellsfargo-everyday-checking",
        metadata={
            "card_issuer": "Wells Fargo",
            "card_product": "Everyday Checking",
            "cardholder": "Alex Example",
        },
    )
    assert parsed["amount"].tolist() == [388.0, -4692.05, 2631.6, 700.0, -0.05]
    assert not parsed["raw_description"].str.contains("VENMO").any()
    assert "check" not in {column.lower() for column in parsed.columns}


def test_wells_fargo_csv_identity_needs_product_and_cardholder(tmp_path: Path) -> None:
    statement = tmp_path / "history.csv"
    _write_history(statement)
    result = detect_statement_identity(statement)
    assert result.issuer == "Wells Fargo"
    assert result.confidence == "product_required"
    assert result.needs_cardholder is True
    assert result.needs_manual_details is True
    assert "account product" in result.message.lower()


def test_wells_product_folder_resolves_csv_parser() -> None:
    assert resolve_parser("wellsfargo-everyday-checking", ".csv") is parse_wells_fargo_csv
    assert resolve_parser("wellsfargo-autograph-visa-signature", ".pdf") is parse_wells_fargo_pdf


def test_extraction_passes_upload_context_to_wells_csv(tmp_path: Path) -> None:
    statement = tmp_path / "inbox" / "wellsfargo-everyday-checking" / "july.csv"
    statement.parent.mkdir(parents=True)
    _write_history(statement)
    write_upload_context(
        statement,
        issuer="Wells Fargo",
        product="Everyday Checking",
        cardholder="Alex Example",
    )
    result = extract_statements(statement.parents[1])
    assert len(result.frame) == 5
    assert result.frame["card_product"].unique().tolist() == ["Everyday Checking"]
    assert result.frame["cardholder"].unique().tolist() == ["Alex Example"]


def test_overlapping_account_history_does_not_double_count(tmp_path: Path) -> None:
    first = tmp_path / "july.csv"
    second = tmp_path / "july-aug.csv"
    _write_history(first)
    _write_history(
        second,
        extra='"08/20/2026","ROUNDPOINT MTG PAYMENTS 080126","-1834.56","","Posted"\n',
    )
    metadata = {
        "card_issuer": "Wells Fargo",
        "card_product": "Everyday Checking",
        "cardholder": "Alex Example",
    }
    left = parse_wells_fargo_csv(first, "wellsfargo-everyday-checking", metadata).assign(
        source_document_id="doc-july"
    )
    right = parse_wells_fargo_csv(second, "wellsfargo-everyday-checking", metadata).assign(
        source_document_id="doc-july-aug"
    )
    ledger = normalize(pd.concat([left, right], ignore_index=True))
    assert len(ledger) == 6
    assert (ledger["raw_description"].str.contains("ROUNDPOINT")).sum() == 1
    assert (ledger["raw_description"].str.contains("Example HOA")).sum() == 1


def test_two_savings_products_keep_identical_interest_rows(tmp_path: Path) -> None:
    row = '"DATE","DESCRIPTION","AMOUNT","CHECK #","STATUS"\n"07/31/2026","INTEREST PAYMENT","0.05","","Posted"\n'
    left = tmp_path / "alex.csv"
    right = tmp_path / "sam.csv"
    left.write_text(row, encoding="utf-8")
    right.write_text(row, encoding="utf-8")
    alex = parse_wells_fargo_csv(
        left,
        "wellsfargo-way2save-savings",
        {"card_issuer": "Wells Fargo", "card_product": "Way2Save Savings"},
    ).assign(source_document_id="alex")
    sam = parse_wells_fargo_csv(
        right,
        "wellsfargo-joint-way2save-savings",
        {"card_issuer": "Wells Fargo", "card_product": "Joint Way2Save Savings"},
    ).assign(source_document_id="sam")
    combined = normalize(pd.concat([alex, sam], ignore_index=True))
    assert len(combined) == 2
    assert combined["txn_id"].nunique() == 2
    assert set(combined["card"]) == {
        "wellsfargo-way2save-savings",
        "wellsfargo-joint-way2save-savings",
    }
