"""Typer CLI: fin ingest | classify | review | build | merchants | serve | publish"""

from __future__ import annotations

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from src import pipeline
from src.merchants import append_merchant, load_merchants
from src.paths import EXPORT_DIR, FINANCE_DB, INBOX, LEDGER_PARQUET
from src.store import write_ledger

app = typer.Typer(
    name="fin",
    help="Local-first statement ingestion, classification, and finance ledger.",
    add_completion=False,
    no_args_is_help=True,
)
merchants_app = typer.Typer(help="Inspect and curate canonical merchant identities.")
app.add_typer(merchants_app, name="merchants")
console = Console()


def _require_ledger() -> pd.DataFrame:
    ledger = pipeline.load_ledger()
    if ledger.empty:
        console.print("[red]No ledger yet. Run: fin ingest && fin classify[/red]")
        raise typer.Exit(code=1)
    return ledger


def _print_counts(counts: dict) -> None:
    console.print(
        "[green]Classified[/green] "
        f"rule={counts.get('rule', 0)}  merchant={counts.get('merchant', 0)}  "
        f"ai_proposed={counts.get('ai', 0)}  manual={counts.get('manual', 0)}  "
        f"open={counts.get('open', 0)}  total={counts.get('total', 0)}"
    )


@app.command()
def ingest() -> None:
    """Parse new inbox statements and append unknown transactions to the ledger."""
    result = pipeline.run_ingest()
    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        for detail in result.get("details") or result.get("failed") or []:
            console.print(f"[red]  {detail}[/red]")
        raise typer.Exit(code=1)
    ingested = int(result.get("ingested") or 0)
    total = int(result.get("total") or 0)
    archived = result.get("archived") or []
    failed = result.get("failed") or []
    if ingested:
        console.print(
            f"[green]Ingested {ingested} new transactions ({total} total) → {result.get('path')}[/green]"
        )
    else:
        message = result.get("message") or (
            f"No new transactions ({total} already in ledger)." if total else "Nothing ingested."
        )
        console.print(f"[yellow]{message}[/yellow]")
    if archived:
        console.print(f"[dim]Processed {len(archived)} statement(s); successful files left the active inbox.[/dim]")
    if failed:
        console.print(f"[red]{len(failed)} statement(s) need attention:[/red]")
        for detail in failed:
            console.print(f"[red]  {detail}[/red]")
    if ingested == 0 and not archived:
        raise typer.Exit(code=0 if total else 1)


@app.command()
def classify(
    with_ai: bool = typer.Option(
        False, "--with-ai", help="Ask Ollama to propose categories for the unclassified tail"
    ),
) -> None:
    """Apply merchants.yaml + rules.yaml; optionally propose AI categories."""
    result = pipeline.run_classify(with_ai=with_ai)
    if "error" in result:
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(code=1)
    _print_counts(result)


@app.command()
def review() -> None:
    """Interactively confirm unclassified / AI-proposed rows; write new rules."""
    from src.review import review as review_loop

    with pipeline.ledger_lock():
        ledger = _require_ledger()
        updated = review_loop(ledger)
        write_ledger(updated)
    console.print(f"[green]Ledger updated → {LEDGER_PARQUET}[/green]")


@app.command()
def build() -> None:
    """Detect recurring bills, reconcile expected bills, rebuild DuckDB + exports."""
    result = pipeline.run_build()
    if "error" in result:
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(code=1)

    _sync_dashboard_data()
    console.print(f"[green]DuckDB[/green] → {result['duckdb']}")
    console.print(f"[green]Exports[/green] → {result['export_dir']}")
    console.print(f"[green]Recurring candidates[/green]: {result['recurring_count']}")

    rows = result.get("reconciliation") or []
    if rows:
        table = Table(title="Expected bill reconciliation")
        for col in rows[0]:
            table.add_column(col)
        for row in rows:
            cells = []
            for col in rows[0]:
                val = row.get(col)
                cells.append("" if val is None or pd.isna(val) else str(val))
            table.add_row(*cells)
        console.print(table)


@app.command("migrate-ids")
def migrate_ids() -> None:
    """Rebuild txn_ids from immutable source fields (one-shot upgrade)."""
    from src.migrate import migrate_file

    with pipeline.ledger_lock():
        count, changed = migrate_file()
    if count == 0:
        console.print("[yellow]No ledger to migrate.[/yellow]")
        return
    if changed:
        console.print(f"[green]Migrated {count} transactions[/green] (backup at ledger.parquet.bak)")
    else:
        console.print(f"[green]Ledger already current[/green] ({count} transactions)")


@merchants_app.command("list")
def merchants_list() -> None:
    """Show curated canonical merchants and their alias counts."""
    entries = load_merchants().get("merchants") or []
    if not entries:
        console.print("[yellow]No canonical merchants defined.[/yellow]")
        return
    table = Table(title=f"Canonical merchants ({len(entries)})")
    table.add_column("Canonical")
    table.add_column("Category")
    table.add_column("Aliases", justify="right")
    for entry in entries:
        category = "/".join(filter(None, [entry.get("category"), entry.get("subcategory")]))
        table.add_row(entry.get("canonical", ""), category or "—", str(len(entry.get("aliases") or [])))
    console.print(table)


@merchants_app.command("unknown")
def merchants_unknown(
    threshold: int = typer.Option(88, help="Fuzzy match threshold (0-100)"),
    with_ai: bool = typer.Option(False, "--with-ai", help="Ask Ollama to propose brand names"),
) -> None:
    """List fuzzy clusters of merchants with no canonical identity."""
    clusters = pipeline.unknown_merchant_clusters(threshold=threshold, with_ai=with_ai)
    if not clusters:
        console.print("[green]Every merchant has a canonical identity.[/green]")
        return
    table = Table(title=f"Unknown merchant clusters ({len(clusters)})")
    table.add_column("Representative")
    table.add_column("Variants", justify="right")
    table.add_column("Txns", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("AI proposal")
    for cluster in clusters:
        table.add_row(
            cluster["representative"],
            str(len(cluster["members"])),
            str(cluster["txn_count"]),
            f"{cluster['total_amount']:.2f}",
            cluster.get("proposed_canonical") or "—",
        )
    console.print(table)


@merchants_app.command("add")
def merchants_add(
    canonical: str = typer.Argument(..., help="Canonical brand name, e.g. Walmart"),
    member: list[str] = typer.Option(
        [], "--member", "-m", help="Normalized merchant variant to fold in (repeatable)"
    ),
    regex: str = typer.Option(None, "--regex", help="Explicit alias regex"),
    category: str = typer.Option(None, "--category"),
    subcategory: str = typer.Option(None, "--subcategory"),
) -> None:
    """Create or extend a canonical merchant, then restamp the ledger."""
    if not member and not regex:
        console.print("[red]Provide at least one --member or --regex.[/red]")
        raise typer.Exit(code=1)

    append_merchant(
        canonical=canonical,
        aliases=[{"regex": regex}] if regex else None,
        members=list(member) or None,
        category=category,
        subcategory=subcategory,
    )
    result = pipeline.recanonicalize()
    console.print(
        f"[green]Saved[/green] {canonical} → {result.get('canonical', 0)}/{result.get('updated', 0)} "
        "ledger rows now canonical"
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address (localhost only by default)"),
    port: int = typer.Option(8787, help="Port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the UI in a browser"),
) -> None:
    """Run the local API + UI server."""
    import threading
    import webbrowser

    import uvicorn

    url = f"http://{host}:{port}"
    console.print(f"[green]Serving[/green] {url}")
    if open_browser and not reload:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run("src.api.app:app", host=host, port=port, reload=reload, log_level="info")


@app.command()
def publish(
    dry_run: bool = typer.Option(False, "--dry-run", help="Build dashboard assets only; do not deploy"),
) -> None:
    """Build the static dashboard; optionally deploy via wrangler pages."""
    import shutil
    import subprocess

    import yaml

    from src.paths import DASHBOARD, PUBLISH_PATH

    if not LEDGER_PARQUET.exists():
        console.print("[red]Run fin build first.[/red]")
        raise typer.Exit(code=1)

    build()

    dashboard_build = DASHBOARD / "dist"
    if not dashboard_build.exists():
        console.print(
            "[yellow]No UI build found.[/yellow] Falling back to the plain static pages.\n"
            "For the React dashboard run: cd ui && npm install && npm run build:static"
        )
        shutil.copytree(DASHBOARD / "public", dashboard_build)
    else:
        _sync_dashboard_data(dashboard_build / "data")

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
    from src.extract import iter_statement_files

    files = iter_statement_files(INBOX)
    console.print(f"Inbox files: {len(files)}")
    for card, path in files[:20]:
        console.print(f"  · {card}/{path.name}")
    if len(files) > 20:
        console.print(f"  … and {len(files) - 20} more")

    ledger = pipeline.load_ledger()
    if ledger.empty:
        console.print("Ledger: (none)")
        return

    counts = pipeline.classification_counts(ledger)
    table = Table(title="Ledger status")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Transactions", str(counts["total"]))
    table.add_row("Rule classified", str(counts["rule"]))
    table.add_row("Merchant default", str(counts["merchant"]))
    table.add_row("AI proposed", str(counts["ai"]))
    table.add_row("Manual", str(counts["manual"]))
    table.add_row("Open", str(counts["open"]))
    table.add_row("Canonical merchants", str(int(ledger["canonical_merchant"].notna().sum())))
    table.add_row(
        "Unknown merchants",
        str(int(ledger.loc[ledger["canonical_merchant"].isna(), "normalized_merchant"].nunique())),
    )
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


def _sync_dashboard_data(dest=None) -> None:
    """Copy the freshly allowlisted export set next to the static bundle."""
    import shutil

    from src.paths import DASHBOARD

    targets = [dest] if dest is not None else [DASHBOARD / "public" / "data"]
    dist_data = DASHBOARD / "dist" / "data"
    if dest is None and (DASHBOARD / "dist").exists():
        targets.append(dist_data)

    for target in targets:
        target.mkdir(parents=True, exist_ok=True)
        # Generated data must not retain a stale full-mode ledger when the
        # user switches back to aggregates-only publishing.
        for stale in target.glob("*.json"):
            stale.unlink()
        for stale in target.glob("*.csv"):
            stale.unlink()
        for path in EXPORT_DIR.glob("*"):
            if path.is_file():
                shutil.copy2(path, target / path.name)


if __name__ == "__main__":
    app()
