"""Clean up messy pending-mask logic from review."""

from __future__ import annotations

import re

import pandas as pd
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from src.classify import append_rule, load_rules

console = Console()


def _escape_regex(merchant: str) -> str:
    tokens = [re.escape(t) for t in merchant.split() if t]
    if not tokens:
        return "(?i)."
    return "(?i)" + r"\s+".join(tokens)


def _needs_review(row: pd.Series) -> bool:
    by = row.get("classified_by")
    if by in ("rule", "manual"):
        return False
    if by == "ai":
        return True
    cat = row.get("category")
    return cat is None or cat == "" or cat == "Uncategorized" or pd.isna(cat)


def review(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    rules = load_rules()
    categories = rules.get("categories") or ["Uncategorized"]

    pending = out[out.apply(_needs_review, axis=1)]
    if pending.empty:
        console.print("[green]No transactions need review.[/green]")
        return out

    console.print(
        f"[cyan]Reviewing {len(pending)} transactions[/cyan] "
        "(Enter accepts proposal, s=skip, q=quit)\n"
    )
    console.print(f"Known categories: {', '.join(categories)}\n")

    for idx, row in pending.iterrows():
        table = Table(show_header=False, box=None)
        table.add_row("Date", str(row["posted_date"]))
        table.add_row("Card", str(row["card"]))
        table.add_row("Amount", f"{float(row['amount']):.2f}")
        table.add_row("Raw", str(row["raw_description"]))
        table.add_row("Merchant", str(row["normalized_merchant"]))
        proposed = row.get("proposed_category") or ""
        proposed_sub = row.get("proposed_subcategory") or ""
        if proposed and not (isinstance(proposed, float) and pd.isna(proposed)):
            table.add_row("AI proposal", f"{proposed} / {proposed_sub}")
        console.print(table)

        default = proposed if proposed and not (isinstance(proposed, float) and pd.isna(proposed)) else "Uncategorized"
        answer = Prompt.ask("Category (s=skip, q=quit)", default=str(default)).strip()
        if answer.lower() == "q":
            console.print("[yellow]Stopped review early.[/yellow]")
            break
        if answer.lower() == "s":
            console.print("[dim]Skipped[/dim]\n")
            continue

        category = answer
        subcategory = Prompt.ask("Subcategory", default=str(proposed_sub or "")).strip()

        out.at[idx, "category"] = category
        out.at[idx, "subcategory"] = subcategory
        out.at[idx, "classified_by"] = "manual"
        out.at[idx, "proposed_category"] = None
        out.at[idx, "proposed_subcategory"] = None

        if Confirm.ask("Save as a reusable rule for this merchant?", default=True):
            pattern = _escape_regex(str(row["normalized_merchant"]))
            append_rule(merchant_regex=pattern, category=category, subcategory=subcategory)
            console.print(f"[green]Rule saved[/green] → {category}/{subcategory}\n")
        else:
            console.print("[dim]Saved on this transaction only[/dim]\n")

    return out
