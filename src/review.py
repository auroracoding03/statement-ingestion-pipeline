"""Interactive CLI review — confirm categories and grow the rules file."""

from __future__ import annotations

import pandas as pd
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from src.classify import append_rule, load_rules, rule_pattern_from_merchant

console = Console()

FINAL_SOURCES = ("rule", "manual", "merchant")


def needs_review(row: pd.Series) -> bool:
    by = row.get("classified_by")
    if by in FINAL_SOURCES:
        return False
    if by == "ai":
        return True
    cat = row.get("category")
    return cat is None or cat == "" or cat == "Uncategorized" or pd.isna(cat)


def _is_blank(value) -> bool:
    return value is None or value == "" or (isinstance(value, float) and pd.isna(value))


def rule_from_row(
    row: pd.Series,
    *,
    category: str,
    subcategory: str = "",
    rule_scope: str = "auto",
) -> dict | None:
    """Build the rule dict review would persist, without writing rules.yaml."""
    if rule_scope == "none":
        return None
    canonical = row.get("canonical_merchant")
    has_canonical = not _is_blank(canonical)
    use_canonical = rule_scope == "canonical" or (rule_scope == "auto" and has_canonical)
    cleaned_sub = " ".join((subcategory or "").split()).strip()
    if use_canonical and has_canonical:
        match = {"merchant_canonical": str(canonical)}
    else:
        match = {"merchant_regex": rule_pattern_from_merchant(str(row.get("normalized_merchant") or ""))}
    return {"match": match, "category": category, "subcategory": cleaned_sub}


def _cluster_key(row: pd.Series) -> tuple[str, str, str, str]:
    canonical = row.get("canonical_merchant")
    if not _is_blank(canonical):
        merchant = str(canonical).strip()
        kind = "canonical"
    else:
        merchant = str(row.get("normalized_merchant") or row.get("raw_description") or "").strip()
        kind = "normalized"
    proposed = "" if _is_blank(row.get("proposed_category")) else str(row.get("proposed_category")).strip()
    sub = "" if _is_blank(row.get("proposed_subcategory")) else str(row.get("proposed_subcategory")).strip()
    return kind, merchant, proposed, sub


def cluster_open_review(ledger: pd.DataFrame, *, limit: int = 50) -> list[dict]:
    """Group open review rows by the merchant identity a saved rule would use."""
    if ledger.empty:
        return []
    pending = ledger[ledger.apply(needs_review, axis=1)]
    if pending.empty:
        return []

    groups: dict[tuple[str, str, str, str], list[pd.Series]] = {}
    for _, row in pending.iterrows():
        groups.setdefault(_cluster_key(row), []).append(row)

    clusters: list[dict] = []
    for (kind, merchant, proposed, sub), rows in groups.items():
        amounts = [float(row.get("amount") or 0) for row in rows]
        representative = max(rows, key=lambda row: float(row.get("amount") or 0))
        clusters.append(
            {
                "key": f"{kind}:{merchant}:{proposed}:{sub}",
                "kind": kind,
                "merchant": merchant,
                "canonical": merchant if kind == "canonical" else None,
                "proposed_category": proposed or None,
                "proposed_subcategory": sub or None,
                "count": len(rows),
                "total_amount": round(sum(amounts), 2),
                "representative_txn_id": str(representative.get("txn_id")),
            }
        )
    clusters.sort(key=lambda item: (-item["count"], -item["total_amount"], item["merchant"]))
    return clusters[:limit]


def review(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    rules = load_rules()
    categories = rules.get("categories") or ["Uncategorized"]

    pending = out[out.apply(needs_review, axis=1)]
    if pending.empty:
        console.print("[green]No transactions need review.[/green]")
        return out

    console.print(
        f"[cyan]Reviewing {len(pending)} transactions[/cyan] "
        "(Enter accepts proposal, s=skip, q=quit)\n"
    )
    console.print(f"Known categories: {', '.join(categories)}\n")

    for idx, row in pending.iterrows():
        # A prior rule save in this session may have already classified this row.
        if not needs_review(out.loc[idx]):
            continue

        table = Table(show_header=False, box=None)
        table.add_row("Date", str(row["posted_date"]))
        table.add_row("Card", str(row["card"]))
        table.add_row("Amount", f"{float(row['amount']):.2f}")
        table.add_row("Raw", str(row["raw_description"]))
        table.add_row("Normalized", str(row["normalized_merchant"]))
        if not _is_blank(row.get("canonical_merchant")):
            table.add_row("Canonical", str(row["canonical_merchant"]))
        proposed = row.get("proposed_category")
        proposed_sub = row.get("proposed_subcategory")
        if not _is_blank(proposed):
            table.add_row("AI proposal", f"{proposed} / {proposed_sub or ''}")
        console.print(table)

        default = proposed if not _is_blank(proposed) else "Uncategorized"
        answer = Prompt.ask("Category (s=skip, q=quit)", default=str(default)).strip()
        if answer.lower() == "q":
            console.print("[yellow]Stopped review early.[/yellow]")
            break
        if answer.lower() == "s":
            console.print("[dim]Skipped[/dim]\n")
            continue

        category = answer
        subcategory = Prompt.ask(
            "Subcategory", default=str("" if _is_blank(proposed_sub) else proposed_sub)
        ).strip()

        out.at[idx, "category"] = category
        out.at[idx, "subcategory"] = subcategory
        out.at[idx, "classified_by"] = "manual"
        out.at[idx, "proposed_category"] = None
        out.at[idx, "proposed_subcategory"] = None

        if Confirm.ask("Save as a reusable rule for this merchant?", default=True):
            from src.classify import _compile_rules, _match_rule

            canonical = row.get("canonical_merchant")
            if not _is_blank(canonical):
                rule = append_rule(
                    merchant_canonical=str(canonical),
                    category=category,
                    subcategory=subcategory,
                )
                console.print(f"[green]Rule saved[/green] canonical={canonical} → {category}/{subcategory}")
            else:
                rule = append_rule(
                    merchant_regex=rule_pattern_from_merchant(str(row["normalized_merchant"])),
                    category=category,
                    subcategory=subcategory,
                )
                console.print(f"[green]Rule saved[/green] → {category}/{subcategory}")

            compiled = _compile_rules({"rules": [rule]})
            applied = 0
            if compiled:
                compiled_rule = compiled[0]
                for other_idx, other in out.iterrows():
                    if other_idx == idx or not needs_review(other):
                        continue
                    canonical_m = str(other.get("canonical_merchant") or "")
                    merchant = str(other.get("normalized_merchant") or "")
                    raw = str(other.get("raw_description") or "")
                    if not _match_rule(
                        compiled_rule, canonical=canonical_m, merchant=merchant, raw=raw
                    ):
                        continue
                    out.at[other_idx, "category"] = compiled_rule["category"]
                    out.at[other_idx, "subcategory"] = compiled_rule["subcategory"]
                    out.at[other_idx, "classified_by"] = "rule"
                    out.at[other_idx, "proposed_category"] = None
                    out.at[other_idx, "proposed_subcategory"] = None
                    applied += 1
            if applied:
                console.print(f"[green]Also classified {applied} matching open item(s)[/green]\n")
            else:
                console.print()
        else:
            console.print("[dim]Saved on this transaction only[/dim]\n")

    return out
