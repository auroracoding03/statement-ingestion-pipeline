"""Typer CLI: fin ingest | classify | review | build | publish | status"""

from __future__ import annotations

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from src.ai_suggest import suggest
from src.classify import classify as apply_rules
from src.extract import extract_all
from src.normalize import normalize
from src.paths import (
    EXPORT_DIR,
    FINANCE_DB,
    INBOX,
    LEDGER_PARQUET,
    PROPOSALS_PARQUET,
    ensure_dirs,
)
from src.recurring import detect_recurring, reconcile
from src.review import review as review_loop
from src.store import export_for_dashboard, rebuild_duckdb, write_ledger, write_reconciliation, write_recurring

app = typer.Typer(
    name="fin",
    help="Local-first statement ingestion, classification, and finance ledger.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _load_ledger() -> pd.DataFrame:
    if not LEDGER_PARQUET.exists():
        console.print("[red]No ledger yet. Run: fin ingest && fin classify[/red]")
        raise typer.Exit(code=1)
    return pd.read_parquet(LEDGER_PARQUET)


@app.command()
def ingest() -> None:
    """Extract + normalize + dedup inbox statements into the ledger skeleton."""
    ensure_dirs()
    raw = extract_all(INBOX)
    if raw.empty:
        console.print("[yellow]Nothing ingested.[/yellow]")
        raise typer.Exit(code=1)
    frame = normalize(raw)
    # Preserve prior classifications when txn_id matches
    if LEDGER_PARQUET.exists():
        prior = pd.read_parquet(LEDGER_PARQUET)
        keep_cols = [
            "txn_id",
            "category",
            "subcategory",
            "classified_by",
            "proposed_category",
            "proposed_subcategory",
        ]
        existing = [c for c in keep_cols if c in prior.columns]
        if len(existing) > 1:
            frame = frame.merge(prior[existing], on="txn_id", how="left")
    else:
        frame["category"] = None
        frame["subcategory"] = None
        frame["classified_by"] = None
        frame["proposed_category"] = None
        frame["proposed_subcategory"] = None

    path = write_ledger(frame)
    console.print(f"[green]Ingested {len(frame)} unique transactions → {path}[/green]")


@app.command()
def classify(
    with_ai: bool = typer.Option(False, "--with-ai", help="Ask Ollama to propose categories for the unclassified tail"),
) -> None:
    """Apply rules.yaml; optionally propose AI categories for leftovers."""
    ensure_dirs()
    ledger = _load_ledger()
    # Re-apply rules to everything not manually locked
    locked = ledger["classified_by"].isin(["manual"])
    unlocked = ledger[~locked].copy()
    locked_rows = ledger[locked].copy()

    classified = apply_rules(unlocked)
    if with_ai:
        classified = suggest(classified)
        if PROPOSALS_PARQUET.parent.exists():
            proposals = classified[classified["classified_by"] == "ai"]
            proposals.to_parquet(PROPOSALS_PARQUET, index=False)

    combined = pd.concat([locked_rows, classified], ignore_index=True)
    # Drop accidental dupes if any
    combined = combined.drop_duplicates(subset=["txn_id"], keep="first")
    write_ledger(combined)

    by_rule = int((combined["classified_by"] == "rule").sum())
    by_ai = int((combined["classified_by"] == "ai").sum())
    by_manual = int((combined["classified_by"] == "manual").sum())
    open_count = int(combined["classified_by"].isna().sum() + (combined["classified_by"] == "").sum())
    console.print(
        f"[green]Classified[/green] rule={by_rule}  ai_proposed={by_ai}  "
        f"manual={by_manual}  open={open_count}  total={len(combined)}"
    )


@app.command()
def review() -> None:
    """Interactively confirm unclassified / AI-proposed rows; write new rules."""
    ledger = _load_ledger()
    updated = review_loop(ledger)
    write_ledger(updated)
    console.print(f"[green]Ledger updated → {LEDGER_PARQUET}[/green]")


@app.command()
def build() -> None:
    """Detect recurring bills, reconcile expected bills, rebuild DuckDB + dashboard exports."""
    ledger = _load_ledger()
    recurring = detect_recurring(ledger)
    reconciliation = reconcile(ledger)
    write_recurring(recurring)
    write_reconciliation(reconciliation)
    db = rebuild_duckdb(ledger, recurring, reconciliation)
    export_dir = export_for_dashboard(ledger, recurring, reconciliation)

    # Copy exports into dashboard/static for local preview / CF publish
    _sync_dashboard_data(export_dir)

    console.print(f"[green]DuckDB[/green] → {db}")
    console.print(f"[green]Exports[/green] → {export_dir}")
    console.print(f"[green]Recurring candidates[/green]: {int(recurring['is_recurring'].sum()) if not recurring.empty else 0}")

    if not reconciliation.empty:
        table = Table(title="Expected bill reconciliation")
        for col in reconciliation.columns:
            table.add_column(col)
        for _, row in reconciliation.iterrows():
            cells = []
            for c in reconciliation.columns:
                val = row[c]
                if val is None or (isinstance(val, float) and pd.isna(val)) or pd.isna(val):
                    cells.append("")
                else:
                    cells.append(str(val))
            table.add_row(*cells)
        console.print(table)


@app.command()
def publish(
    dry_run: bool = typer.Option(False, "--dry-run", help="Build dashboard assets only; do not deploy"),
) -> None:
    """Build static dashboard assets; optionally deploy via wrangler pages."""
    import shutil
    import subprocess
    from src.paths import DASHBOARD, PUBLISH_PATH
    import yaml

    if not LEDGER_PARQUET.exists():
        console.print("[red]Run fin build first.[/red]")
        raise typer.Exit(code=1)

    # Ensure latest exports
    build()

    dashboard_build = DASHBOARD / "dist"
    if dashboard_build.exists():
        shutil.rmtree(dashboard_build)
    shutil.copytree(DASHBOARD / "public", dashboard_build)
    # data already synced into public/data by build()

    console.print(f"[green]Static site ready[/green] → {dashboard_build}")

    if dry_run:
        console.print("[dim]Dry run — skipping Cloudflare deploy.[/dim]")
        return

    project = "statement-ingestion-dashboard"
    if PUBLISH_PATH.exists():
        with PUBLISH_PATH.open() as f:
            project = (yaml.safe_load(f) or {}).get("cloudflare", {}).get("project_name", project)

    wrangler = shutil.which("wrangler")
    if not wrangler:
        console.print(
            "[yellow]wrangler not found.[/yellow] Install with: npm i -g wrangler\n"
            f"Then: wrangler pages deploy {dashboard_build} --project-name {project}"
        )
        raise typer.Exit(code=0)

    subprocess.run(
        [wrangler, "pages", "deploy", str(dashboard_build), "--project-name", project],
        check=False,
    )


@app.command()
def status() -> None:
    """Show ledger / inbox / classification summary."""
    ensure_dirs()
    from src.extract import iter_statement_files

    files = iter_statement_files(INBOX)
    console.print(f"Inbox files: {len(files)}")
    for card, path in files[:20]:
        console.print(f"  · {card}/{path.name}")
    if len(files) > 20:
        console.print(f"  … and {len(files) - 20} more")

    if not LEDGER_PARQUET.exists():
        console.print("Ledger: (none)")
        return

    ledger = pd.read_parquet(LEDGER_PARQUET)
    table = Table(title="Ledger status")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Transactions", str(len(ledger)))
    table.add_row("Rule classified", str(int((ledger.get("classified_by") == "rule").sum())))
    table.add_row("AI proposed", str(int((ledger.get("classified_by") == "ai").sum())))
    table.add_row("Manual", str(int((ledger.get("classified_by") == "manual").sum())))
    open_n = int(ledger["classified_by"].isna().sum()) if "classified_by" in ledger.columns else 0
    table.add_row("Open", str(open_n))
    table.add_row("DuckDB", "yes" if FINANCE_DB.exists() else "no")
    table.add_row("Exports", "yes" if EXPORT_DIR.exists() else "no")
    console.print(table)


@app.command("run-all")
def run_all(
    with_ai: bool = typer.Option(False, "--with-ai"),
    skip_review: bool = typer.Option(False, "--skip-review"),
) -> None:
    """ingest → classify → (review) → build."""
    ingest()
    classify(with_ai=with_ai)
    if not skip_review:
        review()
    build()


def _sync_dashboard_data(export_dir) -> None:
    import shutil
    from src.paths import DASHBOARD

    dest = DASHBOARD / "public" / "data"
    dest.mkdir(parents=True, exist_ok=True)
    for path in export_dir.glob("*"):
        if path.is_file():
            shutil.copy2(path, dest / path.name)


if __name__ == "__main__":
    app()
