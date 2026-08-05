# Statement Ingestion Pipeline

Local-first credit-card statement ingestion, merchant canonicalization, classification, recurring-bill detection, and a finance dashboard.

Raw statements, `rules.yaml`, and `merchants.yaml` stay on your machine. The published dashboard is a thin read-only consumer of exported aggregates (optionally line items) and can go to Cloudflare Pages behind Access.

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
fin classify            # merchants.yaml + rules.yaml
fin classify --with-ai  # optional: Ollama proposals for the unclassified tail
fin review              # confirm proposals; writes new rules
fin build               # recurring + DuckDB + dashboard exports
fin status
```

### Interactive UI

```bash
cd ui && npm install && npm run build && cd ..
fin serve               # http://127.0.0.1:8787
```

`fin serve` binds to localhost only and serves the React app plus the `/api/*` routes that drive ingest, the review queue, and merchant curation. During UI development, run `npm run dev` in `ui/` (port 5173, proxies `/api` to 8787).

CLI entrypoint: `fin` (also `python -m src.cli`).

## Merchant identity

Three layers, from immutable to curated:

| Layer | Example | Owner |
| --- | --- | --- |
| `raw_description` | `WAL-MART #1234 ATLANTA GA` | the statement |
| `normalized_merchant` | `WAL-MART ATLANTA GA` | `src/normalize.py` |
| `canonical_merchant` | `Walmart` | `config/merchants.yaml` |

`config/merchants.yaml` is a durable asset, the counterpart to `rules.yaml`. One canonical entry collapses every statement variant (`W*LMART`, `WLMRT`, `WAL-MART #1234`) into a single brand, so a single `canonical: Walmart` rule replaces a pile of variant regexes.

Merchants with no alias match get grouped into fuzzy clusters (`rapidfuzz`, tunable threshold) and surfaced in the Merchants page ranked by spend. The local model can propose a brand name for each cluster, but nothing is written until you confirm it. `merchant_source` records how each row got its name: `alias`, `manual`, `ai`, or `none`.

```bash
fin merchants list
fin merchants unknown --with-ai
fin merchants add "Local Coffee Roasters" -m "LOCAL COFFEE ROASTERS DOWNTOWN" --category Food
```

## Manual obligations

Predictable monthly expenses that never appear on a credit-card statement (mortgages paid from checking, insurance drafts, etc.) live on the local **Obligations** page — not in the ledger.

- Definitions are stored in `config/manual_obligations.yaml`.
- Month-by-month confirmations are stored in `data/manual_obligation_occurrences.json`.
- **Expected amounts never count as spending.** Only a manually confirmed `paid` occurrence does.
- Confirmed payments are merged into local category totals (`GET /api/categories/monthly`) so Overview and Categories reflect them.
- They are **not** written into `ledger.parquet`, Transactions, recurring detection, DuckDB, or the static Cloudflare export.
- Do **not** re-enter expenses that already appear on an imported card statement — that would double-count.
- V1 supports **monthly** cadence only (due day 1–28).

`config/expected_recurring.yaml` remains the statement-reconciliation watchlist and is unrelated to this feature.

## Classification model

Precedence, first match wins:

1. `rules.yaml` rule on `merchant_canonical`
2. `rules.yaml` rule on `merchant_exact` or `merchant_regex` (checked against canonical, then normalized, then raw)
3. Default `category` declared on the merchant in `merchants.yaml`
4. Ollama proposal for whatever is left — **advisory only**, confirmed via `fin review` or the UI

## Transaction ids

`txn_id` = sha256(`card|posted_date|amount|raw_description|seq`)[:16].

It is deliberately hashed from the *immutable* source text rather than a derived merchant field, so retuning normalization or canonicalization never churns ids or breaks dedup. `seq` is a within-group ordinal, so two genuinely identical same-day purchases stay two rows while a re-imported file still collapses.

Ledgers written before this change are upgraded automatically on load, or explicitly:

```bash
fin migrate-ids   # rewrites ids, keeps categories, backs up to ledger.parquet.bak
```

## Publish modes

In `config/publish.yaml`:

- `aggregates_only` (default) — category totals, recurring, reconciliation, and merchant totals leave the machine
- `full` — also includes line-item `ledger.json`

The same React codebase produces both builds. `npm run build:static` emits a read-only bundle into `dashboard/dist` that reads `./data/*.json` instead of the API, with every write control hidden.

```bash
cd ui && npm run build:static && cd ..
fin publish --dry-run   # build only
fin publish             # wrangler pages deploy
```

Put Cloudflare Access in front of the Pages project before sharing the URL.

## Layout

```
inbox/<card>/*.csv|pdf   # immutable inputs (gitignored)
config/rules.yaml        # durable classification asset
config/merchants.yaml    # durable canonical merchant asset
config/manual_obligations.yaml  # local non-card monthly obligations
config/expected_recurring.yaml
config/publish.yaml      # full | aggregates_only
config/parsers/          # per-issuer CSV/PDF parsers
src/                     # extract → normalize → canonicalize → classify → ai → review → recurring → store
src/obligations.py       # manual obligation definitions + confirmations
src/api/                 # FastAPI app behind fin serve
ui/                      # Vite + React UI (live and static builds)
data/                    # ledger.parquet, obligation occurrences, export/ (gitignored)
dashboard/dist/          # static publish artifact (gitignored)
fixtures/                # sample CSVs
```

## Concurrency

`ledger.parquet` is read-modify-write, so both the CLI and the API take a `filelock` on `data/ledger.lock` before mutating. A UI action and a CLI run can safely overlap.

## Tests

```bash
pytest                          # pipeline, merchants, API
cd ui && npm run typecheck      # UI
```

Tests never touch the real `config/` — `tests/conftest.py` redirects config paths to a temp copy unless a test opts in with the `real_config` marker.

## Adding an issuer parser

1. Add `config/parsers/<issuer>_csv.py` (and/or PDF)
2. Register it in `config/parsers/__init__.py`
3. Drop files under `inbox/<issuer>/`
