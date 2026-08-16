"""FastAPI app backing the local UI.

Binds to localhost by default. There is no auth layer here on purpose: the
service is loopback-only and the published Cloudflare build is a separate,
read-only static export.
"""

from __future__ import annotations

import math
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src import pipeline
from src import ai_review
from src.ai_suggest import ollama_available, recommended_config, start_ollama_serve
from src.atomic import atomic_copy_stream
from src.api import jobs
from src.api.schemas import (
    AIAnalyzeRequest,
    AIProposalDecisionsRequest,
    BudgetPut,
    CardholderAssignIn,
    CardProductIn,
    CategoryDelete,
    CategoryIn,
    ClassifyRequest,
    BulkTransactionsRequest,
    InsightsChatRequest,
    MerchantIn,
    MerchantMerge,
    MerchantUpdate,
    ReviewDecision,
    ReviewPreviewRequest,
    RuleIn,
    RuleUpdate,
    SubcategoryIn,
    TagIn,
    UploadCommitRequest,
)
from src.insights import (
    InsightsError,
    InsightsSandboxError,
    assert_loopback_ollama_host,
    project_ledger_view,
    run_insights_turn,
)
from src.budget import list_budget, save_envelopes
from src.cashflow import non_payment_frame
from src.classify import (
    append_category,
    append_rule,
    append_subcategory,
    delete_rule,
    list_subcategories,
    load_rules,
    rewrite_merchant_canonical,
    update_rule,
)
from src.extract import iter_statement_files
from src.merchants import append_merchant, delete_merchant, load_merchants, merge_merchants, update_merchant
from src.cards import build_cards_coverage
from src.overview import _filter_cardholder, build_period_summary, cardholders
from src.taxonomy import category_impact, delete_category
from src.paths import DASHBOARD, EXPORT_DIR, FINANCE_DB, INBOX, PENDING_UPLOADS, UI, ensure_dirs
from src.periods import PRESETS, filter_posted
from src.recurring import detect_recurring
from src.review import cluster_open_review, needs_review, rule_from_row
from src.statement_identity import detect_statement_identity
from src.store import last_statement_upload_at
from src.tags import create_tag, delete_tag, list_tags, normalize_tag_ids
from src.updater import UpdateError, check_for_update, install_latest_update
from src.upload_context import (
    append_card_product,
    card_key,
    list_card_products,
    normalize_cardholder,
    normalize_issuer,
    normalize_product,
    resolve_card_product_for_issuer,
    write_upload_context,
)
from src.version import APP_VERSION

app = FastAPI(title="Statement Ingestion Pipeline", version=APP_VERSION)

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
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        try:
            as_list = value.tolist()
            if isinstance(as_list, list):
                return [_jsonable(v) for v in as_list]
        except (TypeError, ValueError):
            pass
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


@app.get("/api/health")
def get_health() -> dict:
    """Lightweight liveness probe for the desktop launcher readiness wait."""
    return {"ok": True, "version": APP_VERSION}


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
        "cardholders": cardholders(ledger),
        "inbox_files": [{"card": card, "name": path.name} for card, path in files],
        "duckdb": FINANCE_DB.exists(),
        "exports": EXPORT_DIR.exists(),
        "ollama_available": ollama_available(),
        "version": APP_VERSION,
        "last_statement_upload_at": last_statement_upload_at(),
    }


# --------------------------------------------------------------------------- local AI


@app.get("/api/ai/status")
def get_ai_status(warmup: bool = False) -> dict:
    """Report local runtime/model state; no financial data leaves the machine."""
    return ai_review.ai_status(warmup=warmup)


@app.post("/api/ai/start")
def post_ai_start() -> dict:
    """Start the local Ollama daemon when it is installed but not running."""
    cfg = recommended_config()
    host = str(cfg.get("host") or "")
    try:
        assert_loopback_ollama_host(host)
    except InsightsSandboxError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        started = start_ollama_serve(host=host)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    status = ai_review.ai_status()
    return {**status, "started": started["started"]}


@app.post("/api/ai/model/pull")
def post_ai_model_pull(background: BackgroundTasks) -> dict:
    return _start(
        "ai-model-pull",
        lambda on_progress: ai_review.pull_model(on_progress=on_progress),
        background,
        progress=True,
    )


@app.post("/api/ai/analyze")
def post_ai_analyze(body: AIAnalyzeRequest, background: BackgroundTasks) -> dict:
    mode = body.mode
    return _start(
        "ai-analyze",
        lambda on_progress: pipeline.run_ai_analysis(mode, on_progress=on_progress),
        background,
        progress=True,
    )


@app.post("/api/insights/chat")
def post_insights_chat(body: InsightsChatRequest) -> dict:
    """Read-only ledger Q&A. POST carries the question; nothing is persisted."""
    cfg = recommended_config()
    host = str(cfg.get("host") or "")
    try:
        assert_loopback_ollama_host(host)
    except InsightsSandboxError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not ollama_available(host):
        raise HTTPException(status_code=503, detail="Local AI is offline. Start Ollama to use Insights.")
    view = project_ledger_view(pipeline.load_ledger())
    try:
        return run_insights_turn([item.model_dump() for item in body.messages], view)
    except InsightsSandboxError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InsightsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/ai/proposals")
def get_ai_proposals(
    status: str | None = Query("pending", pattern="^(pending|deferred|applied|rejected)$"),
    kind: str | None = Query(None, pattern="^(merchant|category)$"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    return ai_review.list_proposals(status=status, kind=kind, limit=limit, offset=offset)


@app.post("/api/ai/proposals/decide")
def post_ai_proposal_decisions(body: AIProposalDecisionsRequest) -> dict:
    try:
        result = pipeline.apply_ai_decisions([item.model_dump(exclude_none=True) for item in body.decisions])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result.get("error"):
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@app.post("/api/ai/applications/rollback")
def post_ai_rollback() -> dict:
    result = pipeline.rollback_ai_application()
    if result.get("error"):
        raise HTTPException(status_code=409, detail=result["error"])
    return result


# --------------------------------------------------------------------------- updates


@app.get("/api/updates")
def get_updates() -> dict:
    try:
        return check_for_update()
    except UpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/updates/install")
def post_install_update() -> dict:
    try:
        return install_latest_update()
    except UpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --------------------------------------------------------------------------- jobs


def _start(kind: str, fn, background: BackgroundTasks, *, progress: bool = False) -> dict:
    job_id = jobs.create_job(kind)
    if progress:
        def body():
            return fn(lambda current, total, message: jobs.set_progress(job_id, current, total, message))

        background.add_task(jobs.run_job, job_id, body)
    else:
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


def _safe_upload_name(upload: UploadFile) -> tuple[str, str]:
    name = SAFE_NAME.sub("-", Path(upload.filename or "statement").name)
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".pdf"}:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {upload.filename}")
    return name, suffix


def _unique_target(directory: Path, name: str) -> Path:
    target = directory / name
    if target.exists():
        original = Path(name)
        target = directory / f"{original.stem}-{uuid.uuid4().hex[:8]}{original.suffix}"
    return target


@app.post("/api/uploads/inspect")
async def inspect_uploads(files: list[UploadFile] = ()) -> dict:
    """Stage documents and return automatic identity detection for each one."""
    ensure_dirs()
    if not files:
        raise HTTPException(status_code=400, detail="Select at least one CSV or PDF statement.")

    staged: list[dict] = []
    for upload in files:
        name, suffix = _safe_upload_name(upload)
        token = uuid.uuid4().hex
        target = PENDING_UPLOADS / f"{token}--{name}"
        try:
            atomic_copy_stream(target, upload.file)
            identity = detect_statement_identity(target)
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Could not save {name}: {exc}") from exc
        staged.append(
            {
                "token": token,
                "name": name,
                "issuer": identity.issuer,
                "product": identity.product,
                "confidence": identity.confidence,
                "message": identity.message,
                "needs_manual_details": identity.needs_manual_details,
                "needs_cardholder": identity.needs_cardholder,
            }
        )
    return {"items": staged}


@app.post("/api/uploads/commit")
def commit_uploads(body: UploadCommitRequest) -> dict:
    """Move inspected statements into their durable issuer folders."""
    ensure_dirs()
    prepared: list[tuple[Path, str, str, str | None, str | None, str]] = []
    for item in body.items:
        candidates = list(PENDING_UPLOADS.glob(f"{item.token}--*"))
        if len(candidates) != 1:
            raise HTTPException(status_code=404, detail="An upload has expired. Select the file again.")
        staged = candidates[0]
        identity = detect_statement_identity(staged)
        try:
            issuer = normalize_issuer(item.issuer) if item.issuer else identity.issuer
            raw_product = item.product if item.product is not None else identity.product
            product = normalize_product(issuer, raw_product)
            holder = normalize_cardholder(item.cardholder) if item.cardholder else None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not issuer:
            raise HTTPException(status_code=422, detail=f"Select an issuer for {staged.name[34:]}")
        _, needs_product = resolve_card_product_for_issuer(issuer, product)
        if needs_product:
            raise HTTPException(
                status_code=422,
                detail=f"Select a card product for {staged.name[34:]} before adding it to the inbox.",
            )
        if issuer == "American Express" and staged.suffix.lower() == ".csv" and not product:
            raise HTTPException(
                status_code=422,
                detail="American Express CSV files need a card product because the export does not include it.",
            )
        if identity.needs_cardholder and not holder:
            raise HTTPException(
                status_code=422,
                detail=f"Select a cardholder for {staged.name[34:]} before adding it to the inbox.",
            )
        card = card_key(issuer, product) if issuer != "Generic" else "generic"
        prepared.append((staged, issuer, card, product, holder, staged.name[34:]))

    written: list[str] = []
    for staged, issuer, card, product, holder, original_name in prepared:
        destination_dir = INBOX / card
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_target(destination_dir, original_name)
        os.replace(staged, target)
        if issuer != "Generic":
            write_upload_context(target, issuer=issuer, product=product, cardholder=holder)
        written.append(f"{card}/{target.name}")
    return {"written": written}


@app.post("/api/upload")
async def post_upload(
    card: str = Query("generic"),
    issuer: str | None = Query(None),
    product: str | None = Query(None),
    cardholder: str | None = Query(None),
    files: list[UploadFile] = (),
) -> dict:
    ensure_dirs()
    try:
        selected_issuer = normalize_issuer(issuer) if issuer else None
        selected_product = normalize_product(selected_issuer, product)
        selected_holder = normalize_cardholder(cardholder) if cardholder else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if selected_issuer == "American Express" and not selected_product:
        raise HTTPException(status_code=422, detail="American Express uploads require a card product")
    safe_card = (
        card_key(selected_issuer, selected_product)
        if selected_issuer and selected_issuer != "Generic"
        else SAFE_NAME.sub("-", card).strip("-").lower() or "generic"
    )
    dest_dir = INBOX / safe_card
    dest_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for upload in files:
        name, _suffix = _safe_upload_name(upload)
        target = _unique_target(dest_dir, name)
        atomic_copy_stream(target, upload.file)
        if selected_issuer:
            write_upload_context(
                target,
                issuer=selected_issuer,
                product=selected_product,
                cardholder=selected_holder,
            )
        written.append(f"{safe_card}/{target.name}")

    return {"card": safe_card, "written": written}


# --------------------------------------------------------------------------- ledger reads


@app.get("/api/transactions")
def get_transactions(
    q: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    tag: str | None = None,
    card: str | None = None,
    merchant: str | None = None,
    unclassified: bool = False,
    since: str | None = None,
    until: str | None = None,
    sort: str = Query(default="posted_date"),
    order: str = Query(default="desc"),
    limit: int = Query(200, le=5000),
    offset: int = 0,
) -> dict:
    ledger = pipeline.load_ledger()
    if ledger.empty:
        return {"total": 0, "items": []}

    frame = ledger
    if "tags" not in frame.columns:
        frame = frame.copy()
        frame["tags"] = [[] for _ in range(len(frame))]
    else:
        frame = frame.copy()
        frame["tags"] = frame["tags"].apply(normalize_tag_ids)

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
    if subcategory:
        frame = frame[frame["subcategory"].fillna("") == subcategory]
    if tag:
        frame = frame[frame["tags"].apply(lambda values: tag in values)]
    if card:
        frame = frame[frame["card"] == card]
    if merchant:
        frame = frame[
            (frame["canonical_merchant"] == merchant) | (frame["normalized_merchant"] == merchant)
        ]
    if unclassified:
        frame = frame[frame.apply(needs_review, axis=1)]
    if since or until:
        try:
            frame = filter_posted(frame, since, until)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    sort_key = sort if sort in {"posted_date", "amount", "card", "merchant", "category"} else "posted_date"
    descending = (order or "desc").lower() != "asc"
    frame = frame.copy()
    if sort_key == "posted_date":
        frame["_sort"] = pd.to_datetime(frame["posted_date"], errors="coerce")
    elif sort_key == "merchant":
        canonical = frame["canonical_merchant"].fillna("").astype(str)
        normalized = frame["normalized_merchant"].fillna("").astype(str)
        frame["_sort"] = canonical.where(canonical.str.strip() != "", normalized).str.lower()
    elif sort_key == "category":
        frame["_sort"] = frame["category"].fillna("Uncategorized").astype(str).str.lower()
    else:
        frame["_sort"] = frame[sort_key]
    frame = frame.sort_values("_sort", ascending=not descending, kind="mergesort")
    frame = frame.drop(columns=["_sort"])

    total = len(frame)
    page = frame.iloc[offset : offset + limit]
    return {"total": total, "items": _records(page)}


@app.post("/api/transactions/bulk")
def post_transactions_bulk(body: BulkTransactionsRequest) -> dict:
    result = pipeline.apply_bulk_transactions(
        body.txn_ids,
        category=body.category,
        subcategory=body.subcategory,
        tags=body.tags,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.delete("/api/transactions/{txn_id}")
def delete_transaction(txn_id: str) -> dict:
    result = pipeline.delete_transaction(txn_id)
    error = result.get("error")
    if error:
        status = 404 if error == "Unknown transaction" else 400
        raise HTTPException(status_code=status, detail=error)
    return result


@app.get("/api/review/queue")
def get_review_queue(limit: int = Query(100, le=1000)) -> dict:
    ledger = pipeline.load_ledger()
    if ledger.empty:
        return {"total": 0, "items": [], "categories": [], "subcategories": {}}

    pending = ledger[ledger.apply(needs_review, axis=1)].sort_values("amount", ascending=False)
    categories = load_rules().get("categories") or []
    return {
        "total": int(len(pending)),
        "items": _records(pending.head(limit)),
        "categories": categories,
        "subcategories": list_subcategories(),
    }


@app.get("/api/review/clusters")
def get_review_clusters(limit: int = Query(50, le=200)) -> dict:
    ledger = pipeline.load_ledger()
    items = cluster_open_review(ledger, limit=limit)
    return {"total": len(items), "items": items}


@app.post("/api/review/preview-rule")
def post_review_preview(body: ReviewPreviewRequest) -> dict:
    ledger = pipeline.load_ledger()
    match = ledger[ledger["txn_id"] == body.txn_id] if not ledger.empty else ledger
    if match.empty:
        raise HTTPException(status_code=404, detail="Unknown transaction")
    spec = rule_from_row(
        match.iloc[0],
        category=body.category,
        subcategory=body.subcategory,
        rule_scope=body.rule_scope,
    )
    if spec is None:
        return {"match_count": 0, "sample": []}
    matches = pipeline.open_matches_for_rule(ledger, spec)
    return {"match_count": len(matches), "sample": matches[:20]}


@app.post("/api/review/{txn_id}")
def post_review(txn_id: str, body: ReviewDecision) -> dict:
    ledger = pipeline.load_ledger()
    match = ledger[ledger["txn_id"] == txn_id]
    if match.empty:
        raise HTTPException(status_code=404, detail="Unknown transaction")
    row = match.iloc[0]

    result = pipeline.apply_review_decision(
        txn_id,
        category=body.category,
        subcategory=body.subcategory,
        tags=body.tags,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    if body.subcategory.strip():
        append_subcategory(body.category, body.subcategory)

    rule = None
    applied_txn_ids: list[str] = []
    spec = rule_from_row(row, category=body.category, subcategory=body.subcategory, rule_scope=body.rule_scope)
    if body.create_rule and spec is not None:
        match_fields = spec["match"]
        rule = append_rule(
            merchant_canonical=match_fields.get("merchant_canonical"),
            merchant_regex=match_fields.get("merchant_regex"),
            category=body.category,
            subcategory=body.subcategory,
        )
        applied_txn_ids = pipeline.apply_rule_to_open_review(rule)

    return {**result, "rule": rule, "applied_txn_ids": applied_txn_ids}


@app.get("/api/recurring")
def get_recurring() -> list[dict]:
    ledger = pipeline.load_ledger()
    if ledger.empty:
        return []
    return _records(detect_recurring(ledger))


@app.get("/api/reconciliation")
def get_reconciliation() -> list[dict]:
    from src.paths import RECONCILE_PARQUET

    if not RECONCILE_PARQUET.exists():
        return []
    return _records(pd.read_parquet(RECONCILE_PARQUET))


@app.get("/api/categories/monthly")
def get_categories_monthly(cardholder: str | None = Query(default=None)) -> list[dict]:
    ledger = pipeline.load_ledger()
    if ledger.empty:
        return []
    scoped = _filter_cardholder(ledger, cardholder.strip() if cardholder and cardholder.strip() else None)
    spend = non_payment_frame(scoped)
    if spend.empty:
        return []
    if "subcategory" not in spend.columns:
        spend["subcategory"] = ""
    frame = (
        spend.assign(
            month=lambda d: pd.to_datetime(d["posted_date"]).dt.strftime("%Y-%m"),
            category=lambda d: d["category"].fillna("Uncategorized"),
            subcategory=lambda d: d["subcategory"].fillna("").astype(str).str.strip(),
        )
        .groupby(["month", "category", "subcategory"], as_index=False)
        .agg(total=("amount", "sum"), txn_count=("amount", "count"))
        .sort_values(["month", "category", "subcategory"])
    )
    return _records(frame)


@app.get("/api/overview/month")
def get_overview_month(
    month: str | None = Query(default=None),
    cardholder: str | None = Query(default=None),
    preset: str = Query(default="month"),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
) -> dict:
    if preset not in PRESETS:
        raise HTTPException(status_code=400, detail="Unknown period preset")
    ledger = pipeline.load_ledger()
    try:
        return build_period_summary(
            ledger,
            preset=preset,
            month=month,
            since=since,
            until=until,
            cardholder=cardholder,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/cards")
def get_cards(
    issuer: str | None = Query(default=None),
    product: str | None = Query(default=None),
    cardholder: str | None = Query(default=None),
) -> dict:
    ledger = pipeline.load_ledger()
    return build_cards_coverage(ledger, issuer=issuer, product=product, cardholder=cardholder)


@app.post("/api/cards/cardholder")
def post_cards_cardholder(body: CardholderAssignIn) -> dict:
    result = pipeline.assign_cardholder(body.issuer, body.product, body.cardholder)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])
    return result


# --------------------------------------------------------------------------- merchants


@app.get("/api/merchants")
def get_merchants() -> dict:
    entries = load_merchants().get("merchants") or []
    ledger = pipeline.load_ledger()

    usage: dict[str, dict] = {}
    if not ledger.empty and "canonical_merchant" in ledger.columns:
        spend = non_payment_frame(ledger)
        grouped = (
            spend[spend["canonical_merchant"].notna()]
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

    yaml_names = {
        str(entry.get("canonical", "")).casefold()
        for entry in entries
        if str(entry.get("canonical") or "").strip()
    }

    def _stats_for(name: str) -> dict:
        if name in usage:
            return usage[name]
        folded = name.casefold()
        for key, stats in usage.items():
            if key.casefold() == folded:
                return stats
        return {"txn_count": 0, "total_amount": 0.0}

    items = []
    for entry in entries:
        canonical = entry.get("canonical", "")
        items.append(
            {
                "canonical": canonical,
                "category": entry.get("category"),
                "subcategory": entry.get("subcategory"),
                "aliases": entry.get("aliases") or [],
                **_stats_for(str(canonical)),
            }
        )
    orphans = [
        {"canonical": name, **stats}
        for name, stats in usage.items()
        if str(name).strip() and name.casefold() not in yaml_names
    ]
    orphans.sort(key=lambda row: (-int(row["txn_count"]), str(row["canonical"]).lower()))
    return {"total": len(items), "items": items, "orphans": orphans}


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


@app.post("/api/merchants/merge")
def post_merchant_merge(body: MerchantMerge) -> dict:
    source = " ".join(body.source.split()).strip()
    target = " ".join(body.target.split()).strip()
    try:
        entry = merge_merchants(source, target)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown target merchant") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    rewrite_merchant_canonical(source, str(entry["canonical"]))
    category = str(entry.get("category") or "").strip() if body.apply_category else ""
    rewritten = pipeline.reassign_canonical_on_ledger(
        source,
        str(entry["canonical"]),
        category=category or None,
        subcategory=str(entry.get("subcategory") or "") if category else "",
    )
    return {
        "merchant": entry,
        "rewritten": rewritten,
        "applied": rewritten if category else 0,
    }


@app.patch("/api/merchants/{canonical}")
def patch_merchant(canonical: str, body: MerchantUpdate) -> dict:
    aliases = None
    if body.aliases is not None:
        aliases = [item.model_dump(exclude_none=True) for item in body.aliases if item.regex or item.exact]
    clear_category = "category" in body.model_fields_set and not str(body.category or "").strip()
    try:
        entry = update_merchant(
            canonical,
            canonical=body.canonical,
            aliases=aliases,
            category=None if clear_category else body.category,
            subcategory=body.subcategory,
            clear_category=clear_category,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown merchant") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    new_name = str(entry.get("canonical") or canonical)
    if new_name != canonical:
        rewrite_merchant_canonical(canonical, new_name)
        pipeline.rename_canonical_on_ledger(canonical, new_name)
    if body.restamp:
        pipeline.recanonicalize()
    applied = 0
    if body.apply_category and entry.get("category"):
        applied = pipeline.apply_category_to_canonical(
            new_name,
            str(entry.get("category")),
            str(entry.get("subcategory") or ""),
        )
    return {"merchant": entry, "applied": applied}


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
        "subcategories": list_subcategories(),
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


@app.patch("/api/rules/{index}")
def patch_rule(index: int, body: RuleUpdate) -> dict:
    try:
        rule = update_rule(index, category=body.category, subcategory=body.subcategory)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if rule is None:
        raise HTTPException(status_code=404, detail="Unknown rule index")
    return {"rule": {"index": index, **rule}}


@app.post("/api/categories")
def post_category(body: CategoryIn) -> dict:
    ensure_dirs()
    try:
        categories = append_category(body.category)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"categories": categories, "subcategories": list_subcategories()}


@app.post("/api/subcategories")
def post_subcategory(body: SubcategoryIn) -> dict:
    ensure_dirs()
    try:
        subcategories = append_subcategory(body.category, body.subcategory)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"subcategories": subcategories}


@app.get("/api/categories/impact")
def get_category_impact(
    category: str = Query(min_length=1),
    subcategory: str | None = None,
) -> dict:
    ledger = pipeline.load_ledger()
    return category_impact(ledger, category, subcategory or None)


@app.post("/api/categories/delete")
def post_category_delete(body: CategoryDelete) -> dict:
    ensure_dirs()
    ledger = pipeline.load_ledger()
    try:
        return delete_category(
            ledger,
            body.category,
            (body.subcategory or "").strip() or None,
            action=body.action,
            reassign_category=body.reassign_category,
            reassign_subcategory=body.reassign_subcategory,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown category: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/budget")
def get_budget() -> dict:
    ensure_dirs()
    return {"envelopes": list_budget()}


@app.put("/api/budget")
def put_budget(body: BudgetPut) -> dict:
    ensure_dirs()
    envelopes = save_envelopes([item.model_dump() for item in body.envelopes])
    return {"envelopes": envelopes}


# --------------------------------------------------------------------------- tags


def _tag_in_use(tag_id: str) -> bool:
    ledger = pipeline.load_ledger()
    if ledger.empty or "tags" not in ledger.columns:
        return False
    return bool(ledger["tags"].apply(lambda values: tag_id in normalize_tag_ids(values)).any())


@app.get("/api/tags")
def get_tags() -> dict:
    ensure_dirs()
    return {"total": len(list_tags()), "items": list_tags()}


@app.post("/api/tags")
def post_tag(body: TagIn) -> dict:
    ensure_dirs()
    try:
        entry = create_tag(label=body.label, kind=body.kind, tag_id=body.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"tag": entry}


@app.delete("/api/tags/{tag_id}")
def remove_tag(tag_id: str) -> dict:
    ensure_dirs()
    if _tag_in_use(tag_id):
        raise HTTPException(
            status_code=409,
            detail=f"Tag {tag_id!r} is still used on ledger transactions.",
        )
    if not delete_tag(tag_id):
        raise HTTPException(status_code=404, detail="Unknown tag")
    return {"deleted": tag_id}


@app.get("/api/card-products")
def get_card_products() -> dict:
    ensure_dirs()
    return {"products": list_card_products()}


@app.post("/api/card-products")
def post_card_product(body: CardProductIn) -> dict:
    ensure_dirs()
    try:
        products = append_card_product(body.issuer, body.product)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"products": products}


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
