"""FastAPI app backing the local UI.

Binds to localhost by default. There is no auth layer here on purpose: the
service is loopback-only and the published Cloudflare build is a separate,
read-only static export.
"""

from __future__ import annotations

import math
import re
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src import pipeline
from src.ai_suggest import ollama_available
from src.api import jobs
from src.api.schemas import ClassifyRequest, MerchantIn, ReviewDecision, RuleIn
from src.classify import append_rule, delete_rule, load_rules
from src.extract import iter_statement_files
from src.merchants import append_merchant, delete_merchant, load_merchants
from src.paths import DASHBOARD, EXPORT_DIR, FINANCE_DB, INBOX, UI, ensure_dirs
from src.review import needs_review

app = FastAPI(title="Statement Ingestion Pipeline", version="0.2.0")

# Vite dev server talks to this API cross-origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _jsonable(value: Any) -> Any:
    """pandas NaN/NaT and numpy scalars are not JSON serializable."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if value is pd.NaT:
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except (ValueError, AttributeError):
            return str(value)
    if isinstance(value, pd.Series):
        return [_jsonable(v) for v in value]
    return value


def _records(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    out = frame.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].astype(str)
    return [{k: _jsonable(v) for k, v in row.items()} for row in out.to_dict(orient="records")]


# --------------------------------------------------------------------------- status


@app.get("/api/status")
def get_status() -> dict:
    ledger = pipeline.load_ledger()
    files = iter_statement_files(INBOX)
    counts = pipeline.classification_counts(ledger) if not ledger.empty else {}

    canonical = 0
    unknown = 0
    if not ledger.empty and "canonical_merchant" in ledger.columns:
        canonical = int(ledger["canonical_merchant"].notna().sum())
        unknown = int(ledger.loc[ledger["canonical_merchant"].isna(), "normalized_merchant"].nunique())

    return {
        "ledger_exists": pipeline.ledger_exists(),
        "counts": counts,
        "canonical_merchants": canonical,
        "unknown_merchants": unknown,
        "review_pending": int(ledger.apply(needs_review, axis=1).sum()) if not ledger.empty else 0,
        "inbox_files": [{"card": card, "name": path.name} for card, path in files],
        "duckdb": FINANCE_DB.exists(),
        "exports": EXPORT_DIR.exists(),
        "ollama_available": ollama_available(),
    }


# --------------------------------------------------------------------------- jobs


def _start(kind: str, fn, background: BackgroundTasks) -> dict:
    job_id = jobs.create_job(kind)
    background.add_task(jobs.run_job, job_id, fn)
    return {"job_id": job_id, "kind": kind, "status": "pending"}


@app.post("/api/ingest")
def post_ingest(background: BackgroundTasks) -> dict:
    return _start("ingest", pipeline.run_ingest, background)


@app.post("/api/classify")
def post_classify(body: ClassifyRequest, background: BackgroundTasks) -> dict:
    return _start("classify", lambda: pipeline.run_classify(with_ai=body.with_ai), background)


@app.post("/api/build")
def post_build(background: BackgroundTasks) -> dict:
    return _start("build", pipeline.run_build, background)


@app.get("/api/jobs")
def get_jobs(limit: int = 20) -> list[dict]:
    return jobs.list_jobs(limit)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job


# --------------------------------------------------------------------------- upload


@app.post("/api/upload")
async def post_upload(card: str = Query("generic"), files: list[UploadFile] = ()) -> dict:
    ensure_dirs()
    safe_card = SAFE_NAME.sub("-", card).strip("-").lower() or "generic"
    dest_dir = INBOX / safe_card
    dest_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for upload in files:
        name = SAFE_NAME.sub("-", Path(upload.filename or "statement").name)
        if not name.lower().endswith((".csv", ".pdf")):
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {upload.filename}")
        target = dest_dir / name
        with target.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        written.append(f"{safe_card}/{name}")

    return {"card": safe_card, "written": written}


# --------------------------------------------------------------------------- ledger reads


@app.get("/api/transactions")
def get_transactions(
    q: str | None = None,
    category: str | None = None,
    card: str | None = None,
    merchant: str | None = None,
    unclassified: bool = False,
    limit: int = Query(200, le=5000),
    offset: int = 0,
) -> dict:
    ledger = pipeline.load_ledger()
    if ledger.empty:
        return {"total": 0, "items": []}

    frame = ledger
    if q:
        needle = q.lower()
        haystack = (
            frame["raw_description"].astype(str).str.lower()
            + " "
            + frame["normalized_merchant"].astype(str).str.lower()
            + " "
            + frame["canonical_merchant"].fillna("").astype(str).str.lower()
        )
        frame = frame[haystack.str.contains(re.escape(needle), na=False)]
    if category:
        frame = frame[frame["category"].fillna("Uncategorized") == category]
    if card:
        frame = frame[frame["card"] == card]
    if merchant:
        frame = frame[
            (frame["canonical_merchant"] == merchant) | (frame["normalized_merchant"] == merchant)
        ]
    if unclassified:
        frame = frame[frame.apply(needs_review, axis=1)]

    frame = frame.sort_values("posted_date", ascending=False)
    total = len(frame)
    page = frame.iloc[offset : offset + limit]
    return {"total": total, "items": _records(page)}


@app.get("/api/review/queue")
def get_review_queue(limit: int = Query(100, le=1000)) -> dict:
    ledger = pipeline.load_ledger()
    if ledger.empty:
        return {"total": 0, "items": [], "categories": []}

    pending = ledger[ledger.apply(needs_review, axis=1)].sort_values("amount", ascending=False)
    categories = load_rules().get("categories") or []
    return {
        "total": int(len(pending)),
        "items": _records(pending.head(limit)),
        "categories": categories,
    }


@app.post("/api/review/{txn_id}")
def post_review(txn_id: str, body: ReviewDecision) -> dict:
    ledger = pipeline.load_ledger()
    match = ledger[ledger["txn_id"] == txn_id]
    if match.empty:
        raise HTTPException(status_code=404, detail="Unknown transaction")
    row = match.iloc[0]

    result = pipeline.apply_review_decision(
        txn_id, category=body.category, subcategory=body.subcategory
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    rule = None
    if body.create_rule and body.rule_scope != "none":
        canonical = row.get("canonical_merchant")
        has_canonical = bool(canonical) and not pd.isna(canonical)
        use_canonical = body.rule_scope == "canonical" or (body.rule_scope == "auto" and has_canonical)
        if use_canonical and has_canonical:
            rule = append_rule(
                merchant_canonical=str(canonical),
                category=body.category,
                subcategory=body.subcategory,
            )
        else:
            tokens = [re.escape(t) for t in str(row["normalized_merchant"]).split() if t]
            pattern = "(?i)" + r"\s+".join(tokens) if tokens else "(?i)."
            rule = append_rule(
                merchant_regex=pattern,
                category=body.category,
                subcategory=body.subcategory,
            )

    return {**result, "rule": rule}


@app.get("/api/recurring")
def get_recurring() -> list[dict]:
    from src.paths import RECURRING_PARQUET

    if not RECURRING_PARQUET.exists():
        return []
    return _records(pd.read_parquet(RECURRING_PARQUET))


@app.get("/api/reconciliation")
def get_reconciliation() -> list[dict]:
    from src.paths import RECONCILE_PARQUET

    if not RECONCILE_PARQUET.exists():
        return []
    return _records(pd.read_parquet(RECONCILE_PARQUET))


@app.get("/api/categories/monthly")
def get_categories_monthly() -> list[dict]:
    ledger = pipeline.load_ledger()
    if ledger.empty:
        return []
    frame = (
        ledger.assign(
            month=lambda d: pd.to_datetime(d["posted_date"]).dt.strftime("%Y-%m"),
            category=lambda d: d["category"].fillna("Uncategorized"),
        )
        .loc[lambda d: d["amount"] > 0]
        .groupby(["month", "category"], as_index=False)
        .agg(total=("amount", "sum"), txn_count=("amount", "count"))
        .sort_values(["month", "category"])
    )
    return _records(frame)


# --------------------------------------------------------------------------- merchants


@app.get("/api/merchants")
def get_merchants() -> dict:
    entries = load_merchants().get("merchants") or []
    ledger = pipeline.load_ledger()

    usage: dict[str, dict] = {}
    if not ledger.empty and "canonical_merchant" in ledger.columns:
        grouped = (
            ledger[ledger["canonical_merchant"].notna()]
            .groupby("canonical_merchant")
            .agg(txn_count=("amount", "count"), total_amount=("amount", "sum"))
        )
        usage = {
            str(name): {
                "txn_count": int(row["txn_count"]),
                "total_amount": round(float(row["total_amount"]), 2),
            }
            for name, row in grouped.iterrows()
        }

    items = []
    for entry in entries:
        canonical = entry.get("canonical", "")
        items.append(
            {
                "canonical": canonical,
                "category": entry.get("category"),
                "subcategory": entry.get("subcategory"),
                "aliases": entry.get("aliases") or [],
                **usage.get(canonical, {"txn_count": 0, "total_amount": 0.0}),
            }
        )
    return {"total": len(items), "items": items}


@app.get("/api/merchants/unknown")
def get_unknown_merchants(
    threshold: int = Query(88, ge=50, le=100),
    with_ai: bool = False,
) -> dict:
    clusters = pipeline.unknown_merchant_clusters(threshold=threshold, with_ai=with_ai)
    return {"total": len(clusters), "items": clusters, "ollama_available": ollama_available()}


@app.post("/api/merchants")
def post_merchant(body: MerchantIn) -> dict:
    aliases = [a.model_dump(exclude_none=True) for a in body.aliases if a.regex or a.exact]
    if not aliases and not body.members:
        raise HTTPException(status_code=400, detail="Provide aliases or members")

    entry = append_merchant(
        canonical=body.canonical,
        aliases=aliases or None,
        members=body.members or None,
        category=body.category,
        subcategory=body.subcategory,
    )

    stamped = 0
    if body.restamp:
        if body.members:
            stamped = pipeline.set_canonical_for_merchants(body.members, body.canonical)
        else:
            pipeline.recanonicalize()

    return {"merchant": entry, "stamped": stamped}


@app.delete("/api/merchants/{canonical}")
def remove_merchant(canonical: str) -> dict:
    if not delete_merchant(canonical):
        raise HTTPException(status_code=404, detail="Unknown merchant")
    pipeline.recanonicalize()
    return {"deleted": canonical}


@app.post("/api/merchants/recanonicalize")
def post_recanonicalize() -> dict:
    return pipeline.recanonicalize()


# --------------------------------------------------------------------------- rules


@app.get("/api/rules")
def get_rules() -> dict:
    doc = load_rules()
    return {
        "categories": doc.get("categories") or [],
        "rules": [{"index": i, **r} for i, r in enumerate(doc.get("rules") or [])],
    }


@app.post("/api/rules")
def post_rule(body: RuleIn) -> dict:
    if not body.merchant_regex and not body.merchant_canonical:
        raise HTTPException(status_code=400, detail="Provide merchant_regex or merchant_canonical")
    rule = append_rule(
        merchant_regex=body.merchant_regex,
        merchant_canonical=body.merchant_canonical,
        category=body.category,
        subcategory=body.subcategory,
    )
    return {"rule": rule}


@app.delete("/api/rules/{index}")
def remove_rule(index: int) -> dict:
    if not delete_rule(index):
        raise HTTPException(status_code=404, detail="Unknown rule index")
    return {"deleted": index}


# --------------------------------------------------------------------------- static UI

_UI_DIST = UI / "dist"
_FALLBACK = DASHBOARD / "public"


@app.get("/")
def root() -> Any:
    for candidate in (_UI_DIST / "index.html", _FALLBACK / "index.html"):
        if candidate.exists():
            return FileResponse(candidate)
    return JSONResponse(
        {
            "message": "UI not built. Run: cd ui && npm install && npm run build",
            "api_docs": "/docs",
        }
    )


if _UI_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_UI_DIST), html=True), name="ui")
elif _FALLBACK.exists():
    app.mount("/", StaticFiles(directory=str(_FALLBACK), html=True), name="dashboard")
