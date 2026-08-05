# Statement Ingestion Pipeline

Local-first credit-card statement ingestion, classification, recurring-bill detection, and a static finance dashboard.

Raw statements and `rules.yaml` stay on your machine. The dashboard is a thin consumer of exported aggregates (optionally line items) and can publish to Cloudflare Pages behind Access.

## Quick start

```bash
cd ~/Projects/statement-ingestion-pipeline
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

# Drop statements into inbox/<card>/
mkdir -p inbox/chase inbox/amex
cp fixtures/sample_chase.csv inbox/chase/2026-01.csv
cp fixtures/sample_amex.csv inbox/amex/2026-01.csv

fin ingest
fin classify          # rules only
fin classify --with-ai  # optional: Ollama proposals for the unclassified tail
fin review            # confirm proposals; writes new rules
fin build             # recurring + DuckDB + dashboard exports
fin status
fin publish --dry-run # builds dashboard/dist
```

CLI entrypoint: `fin` (also `python -m src.cli`).

## Layout

```
inbox/<card>/*.csv|pdf   # immutable inputs (gitignored)
config/rules.yaml        # durable classification asset
config/expected_recurring.yaml
config/publish.yaml      # full | aggregates_only
config/parsers/          # per-issuer CSV/PDF parsers
src/                     # extract → normalize → classify → ai → review → recurring → store
data/                    # ledger.parquet, finance.duckdb, export/ (gitignored)
dashboard/public/        # static dashboard
fixtures/                # sample CSVs
```

## Classification model

1. Ordered regex / merchant rules in `config/rules.yaml` (first match wins)
2. Optional Ollama suggestions for the unclassified tail (**proposals only**)
3. `fin review` confirms and can append a new rule for next run

Stable `txn_id` = sha256(`card|date|amount|normalized_merchant`)[:16] so re-imports dedupe cleanly.

## Publish modes

In `config/publish.yaml`:

- `aggregates_only` (default) — category totals + recurring + reconciliation leave the machine
- `full` — also includes line-item `ledger.json`

Deploy when ready:

```bash
npm i -g wrangler
fin publish
# or: wrangler pages deploy dashboard/dist --project-name statement-ingestion-dashboard
```

Put Cloudflare Access in front of the Pages project before sharing the URL.

## Adding an issuer parser

1. Add `config/parsers/<issuer>_csv.py` (and/or PDF)
2. Register it in `config/parsers/__init__.py`
3. Drop files under `inbox/<issuer>/`
