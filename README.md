# Statement Ingestion Pipeline

Local-first credit-card statement ingestion, merchant canonicalization, classification, recurring-bill detection, and a finance dashboard.

Raw statements, `rules.yaml`, and `merchants.yaml` stay on your machine. The published dashboard is a thin read-only consumer of explicitly allowlisted aggregates (optionally line items) and can go to Cloudflare Pages behind Access.

## Windows installation

For normal desktop use, download `StatementPipelineSetup.exe` from the release,
run it, and click **Install**. Launch **Statement Pipeline** from the Start menu;
it opens in its own native Windows window. Python, Node.js, and a terminal are
not required on the installed machine.

The current unsigned installer may show a Windows SmartScreen reputation prompt.
Choose **More info** then **Run anyway** only when you downloaded the installer
from a release you trust.

Your statements, configuration, and local database are stored in
`%LOCALAPPDATA%\Statement Pipeline`, not in the program-installation folder.
Upgrades and normal uninstalls preserve this folder. Back it up if you want a
copy of your financial history; delete it manually only if you intend to erase
that data permanently.

Use the **Ingestion** page to add statement files. PDFs are identified from the
statement text and normally need no issuer or card-product input. CSV exports
are identified from their headers when possible; a card product is requested
only when the file omits it, such as an American Express CSV export.

### Building an installer

Windows release builds must be created on Windows. Install Python 3.11+, Node
22.12+, and Inno Setup 6, then run:

```powershell
.\packaging\build-windows.ps1
```

The installer is written to `dist\installer\StatementPipelineSetup.exe`.

After installation, select **Check for updates** in the app header to download
and silently install a newer GitHub Release. The app verifies the release's
SHA-256 checksum before updating, closes, installs the replacement in place,
and restarts. Your data under `%LOCALAPPDATA%\Statement Pipeline` is retained.

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

### Local AI assistant (Windows / Radeon RX 6900 XT)

The **AI assistant** page is a high-throughput local review queue for a large
transaction history. It is designed for an RX 6900 XT (16 GB VRAM) and uses
`qwen3.5:9b` (6.6 GB) through Ollama. The app batches merchant profiles rather
than making one request per transaction, then keeps suggestions in a local
checkpointed queue for review.

1. Update the desktop app, then open **AI assistant**.
2. Install [Ollama for Windows](https://ollama.com/download/windows) if needed.
3. Choose **Download recommended model**. The page warms up the model and
   confirms that it occupies GPU memory. On RX 6000 cards, Ollama's Vulkan
   backend is the expected Windows fallback if ROCm is unavailable.
4. Run **Analyze new or changed data**, review merchant identity proposals
   first, and approve only the aliases you want saved. Then review category
   proposals. Mixed-use merchants such as Walmart remain transaction-level
   suggestions unless prior reviewed history is consistent.

Statements, descriptions, model prompts, and proposals never leave the local
machine. The per-merchant **Look up** button displays the exact browser query
and requires confirmation; it sends only that merchant text, never an amount,
date, card, or statement data. Each approval batch has a local snapshot and
can be undone from the AI assistant page.

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

## Classification model

Precedence, first match wins:

1. `rules.yaml` rule on `merchant_canonical`
2. `rules.yaml` rule on `merchant_exact` or `merchant_regex` (checked against canonical, then normalized, then raw)
3. Default `category` declared on the merchant in `merchants.yaml`
4. Ollama proposal for whatever is left — **advisory only**, confirmed via `fin review` or the UI

## Transaction ids

`txn_id` = sha256(`card|posted_date|amount|raw_description|occurrence`)[:16].

It is deliberately hashed from immutable source text rather than a derived merchant field, so retuning normalization or canonicalization never churns ids. Each statement file is fingerprinted by SHA-256 and recorded in `data/ingestion_manifest.parquet`; `data/transaction_sources.parquet` records every document-to-transaction link. Identical files are skipped. For distinct, overlapping exports, transactions are reconciled as a multiset: two genuine identical purchases survive, but a second statement containing the same two purchases does not double-count them. If any statement fails validation, the full batch is rejected and the existing ledger remains untouched.

Ledgers written before this change are upgraded automatically on load, or explicitly:

```bash
fin migrate-ids   # rewrites ids, keeps categories, backs up to ledger.parquet.bak
```

## Publish modes

In `config/publish.yaml`:

- `aggregates_only` (default) — category totals and redacted recurring/reconciliation aggregates leave the machine
- `full` — also includes line-item `ledger.json`

Aggregate builds are created in a clean staging directory and include only an allowlisted set of artifacts. They never include raw descriptions, transaction IDs, source paths, or the review queue. Merchant and expected-bill names are redacted by default; set `include_merchant_names: true` only for a trusted audience.

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
config/expected_recurring.yaml
config/publish.yaml      # full | aggregates_only
config/parsers/          # per-issuer CSV/PDF parsers
src/                     # extract → normalize → canonicalize → classify → ai → review → recurring → store
src/api/                 # FastAPI app behind fin serve
ui/                      # Vite + React UI (live and static builds)
data/                    # ledger.parquet, finance.duckdb, export/ (gitignored)
dashboard/dist/          # static publish artifact (gitignored)
fixtures/                # sample CSVs
```

## Concurrency

`ledger.parquet` is read-modify-write, so both the CLI and the API take a `filelock` on `data/ledger.lock` before mutating. Ledger, derived Parquet tables, generated exports, DuckDB rebuilds, and curated YAML files are written through staged atomic replacement. A failed parser or interrupted generated-data write therefore leaves the prior valid version available.

## Tests

```bash
pytest                          # pipeline, parser, privacy, and API coverage
cd ui && npm run typecheck && npm run build && npm run build:static
cd ui && npm audit --audit-level=moderate
```

Tests never touch the real `config/` — `tests/conftest.py` redirects config paths to a temp copy unless a test opts in with the `real_config` marker.

## Adding an issuer parser

1. Add `config/parsers/<issuer>_csv.py` (and/or PDF)
2. Register it in `config/parsers/__init__.py`
3. Drop files under `inbox/<issuer>/`
