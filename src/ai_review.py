"""Durable, local-only AI assistance for merchant and category review.

The model never writes the ledger or curated YAML files.  It produces a
checkpointed proposal queue; only an explicit UI/API approval can apply a
change.  Requests are intentionally made for merchant *profiles* in batches,
not for every transaction in a statement history.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from src import paths
from src.ai_suggest import load_ollama_config, ollama_available
from src.atomic import atomic_copy_file, atomic_write_parquet, atomic_write_text
from src.classify import append_rule, load_rules
from src.merchants import append_merchant, canonicalize, cluster_unknowns


PROMPT_VERSION = "merchant-review-v3"
RECOMMENDED_MODEL = "qwen3.5:9b"
GAS_STATION_HINT = (
    "Gas stations and fuel brands (Shell, BP, Exxon, Chevron, Circle K, Speedway, "
    "Love's, Loves, Sheetz, QuikTrip, Wawa, Racetrack, Costco Gas, etc.) must use "
    "category Transport and subcategory Gas — never Food, Shopping, or Retail, "
    "even when the site also sells convenience food."
)
QUOTED_BRAND_RE = re.compile(r"""['\"]([^'\"]{2,60})['\"]""")
PROPOSAL_COLUMNS = [
    "proposal_id",
    "input_fingerprint",
    "kind",
    "status",
    "members_json",
    "txn_ids_json",
    "recommendation_json",
    "evidence_json",
    "confidence",
    "model",
    "prompt_version",
    "batch_id",
    "created_at",
    "updated_at",
    "error",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _unjson(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _proposal_path() -> Path:
    return paths.AI_PROPOSALS_PARQUET


def recommended_config() -> dict:
    """Keep legacy ``llama3.2`` defaults from silently defeating setup.

    Old installations carry their user config forward on upgrade.  That value
    was the application's historical default rather than a deliberate model
    selection, so use the v0.3 recommended model for this new workflow while
    retaining a genuinely custom model choice.
    """
    cfg = load_ollama_config()
    if str(cfg.get("model") or "") == "llama3.2":
        cfg["model"] = RECOMMENDED_MODEL
        cfg["temperature"] = 0
        cfg["num_ctx"] = 8192
        cfg["keep_alive"] = "10m"
    return cfg


def _applications_path() -> Path:
    return paths.AI_APPLICATIONS_PATH


def _snapshot_root() -> Path:
    return paths.AI_SNAPSHOTS


def load_proposals() -> pd.DataFrame:
    path = _proposal_path()
    if not path.exists():
        return pd.DataFrame(columns=PROPOSAL_COLUMNS)
    try:
        frame = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - a damaged cache should not block the ledger
        return pd.DataFrame(columns=PROPOSAL_COLUMNS)
    for column in PROPOSAL_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[PROPOSAL_COLUMNS]


def _write_proposals(frame: pd.DataFrame) -> None:
    paths.ensure_dirs()
    out = frame.copy()
    for column in PROPOSAL_COLUMNS:
        if column not in out.columns:
            out[column] = None
    atomic_write_parquet(out[PROPOSAL_COLUMNS], _proposal_path())


def _fingerprint(kind: str, value: dict) -> str:
    return hashlib.sha256(f"{PROMPT_VERSION}|{kind}|{_json(value)}".encode()).hexdigest()[:24]


def _normal_key(row: pd.Series) -> str:
    canonical = str(row.get("canonical_merchant") or "").strip()
    return canonical.casefold() if canonical else str(row.get("normalized_merchant") or "").casefold()


def _is_open(row: pd.Series) -> bool:
    category = row.get("category")
    return (
        row.get("classified_by") not in ("manual", "rule", "merchant")
        or category is None
        or category == ""
        or category == "Uncategorized"
        or pd.isna(category)
    )


def _profile_unknown_merchants(ledger: pd.DataFrame) -> list[dict]:
    unresolved = ledger[
        ledger["canonical_merchant"].isna() | (ledger["canonical_merchant"] == "")
    ].copy()
    if unresolved.empty:
        return []
    profiles: list[dict] = []
    # Reuse the existing local fuzzy clusterer so spelling/punctuation variants
    # are presented together.  The model still sees every source member and a
    # human approves the resulting alias before it is made durable.
    for cluster in cluster_unknowns(ledger, threshold=88):
        members = sorted({str(v) for v in cluster["members"] if str(v).strip()})
        if not members:
            continue
        rows = unresolved[unresolved["normalized_merchant"].isin(members)]
        profiles.append(
            {
                "key": members[0],
                "members": members,
                "txn_ids": rows["txn_id"].astype(str).tolist(),
                "sample_raw": [str(v) for v in rows["raw_description"].dropna().head(3)],
                "txn_count": int(len(rows)),
                "total_amount": round(float(rows["amount"].sum()), 2),
            }
        )
    return sorted(profiles, key=lambda p: abs(p["total_amount"]), reverse=True)


def _category_profiles(ledger: pd.DataFrame) -> list[dict]:
    open_rows = ledger[ledger.apply(_is_open, axis=1)].copy()
    if open_rows.empty:
        return []
    all_rows = ledger.copy()
    all_rows["_key"] = all_rows.apply(_normal_key, axis=1)
    open_rows["_key"] = open_rows.apply(_normal_key, axis=1)
    profiles: list[dict] = []
    for key, rows in open_rows.groupby("_key", dropna=False):
        history = all_rows[(all_rows["_key"] == key) & all_rows["category"].notna()]
        history = history[history["classified_by"].isin(["manual", "rule", "merchant"])]
        categories = history["category"].astype(str).value_counts()
        consensus_category = None
        consensus_ratio = 0.0
        if len(history) >= 3 and not categories.empty:
            consensus_category = str(categories.index[0])
            consensus_ratio = float(categories.iloc[0] / len(history))
        representative = str(rows.iloc[0].get("canonical_merchant") or rows.iloc[0]["normalized_merchant"])
        profiles.append(
            {
                "key": str(key),
                "merchant": representative,
                "canonical": str(rows.iloc[0].get("canonical_merchant") or "") or None,
                "txn_ids": rows["txn_id"].astype(str).tolist(),
                "sample_raw": [str(v) for v in rows["raw_description"].dropna().head(3)],
                "txn_count": int(len(rows)),
                "total_amount": round(float(rows["amount"].sum()), 2),
                "history_count": int(len(history)),
                "history_category": consensus_category,
                "history_ratio": round(consensus_ratio, 3),
            }
        )
    return sorted(profiles, key=lambda p: abs(p["total_amount"]), reverse=True)


def _schema(kind: str) -> dict:
    if kind == "merchant":
        item = {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "canonical": {"type": "string"},
                "category": {"type": "string"},
                "subcategory": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "reason": {"type": "string"},
                "ambiguous": {"type": "boolean"},
            },
            "required": ["key", "canonical", "category", "subcategory", "confidence", "reason", "ambiguous"],
        }
    else:
        item = {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "category": {"type": "string"},
                "subcategory": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "reason": {"type": "string"},
                "ambiguous": {"type": "boolean"},
            },
            "required": ["key", "category", "subcategory", "confidence", "reason", "ambiguous"],
        }
    return {"type": "object", "properties": {"items": {"type": "array", "items": item}}, "required": ["items"]}


def _looks_like_category_label(value: str, categories: list[str]) -> bool:
    """True when a brand field is clearly taxonomy text rather than a brand name."""
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        return False
    folded = cleaned.casefold()
    allowed = {c.casefold() for c in categories}
    if folded in allowed:
        return True
    # Models often emit "Food/Shopping", "Transport / Gas", or "Food or Shopping".
    separators = ("/", "|", " or ", " / ")
    for sep in separators:
        if sep in folded:
            parts = [p.strip() for p in folded.split(sep) if p.strip()]
            if parts and all(part in allowed or part in {"gas", "retail", "fastfood"} for part in parts):
                return True
    return False


def _sanitize_merchant_answer(answer: dict, categories: list[str]) -> dict:
    """Keep category values out of the brand field and validate category membership."""
    out = dict(answer)
    canonical = str(out.get("canonical") or "").strip()
    category = str(out.get("category") or "").strip()
    subcategory = str(out.get("subcategory") or "").strip()
    if _looks_like_category_label(canonical, categories):
        # Recover when the model swapped fields: taxonomy in canonical, brand empty.
        if not category and canonical.casefold() in {c.casefold() for c in categories}:
            category = next(c for c in categories if c.casefold() == canonical.casefold())
        out["canonical"] = ""
        out["confidence"] = "low"
        out["ambiguous"] = True
        reason = str(out.get("reason") or "").strip()
        note = "Model returned a category label as the brand name."
        out["reason"] = f"{reason} {note}".strip() if reason else note
    if category and category not in categories:
        out["category"] = ""
        out["subcategory"] = ""
        out["confidence"] = "low"
        out["ambiguous"] = True
    else:
        out["category"] = category
        out["subcategory"] = subcategory if category else ""
    out["canonical"] = str(out.get("canonical") or "").strip()
    if not out["canonical"]:
        recovered = _recover_quoted_brand(str(out.get("reason") or ""), categories)
        if recovered:
            out["canonical"] = recovered
    return out


def _recover_quoted_brand(reason: str, categories: list[str]) -> str:
    """Pull a brand the model named in quotes when canonical was left empty."""
    matches = [m.strip() for m in QUOTED_BRAND_RE.findall(reason) if m.strip()]
    for candidate in matches:
        if _looks_like_category_label(candidate, categories):
            continue
        if len(candidate.split()) > 8:
            continue
        return candidate
    return ""


def _category_vocab_line(categories: list[str]) -> str:
    rules = load_rules()
    subs = rules.get("subcategories") or {}
    parts: list[str] = []
    for category in categories:
        bucket = [str(s) for s in (subs.get(category) or []) if str(s).strip()]
        parts.append(f"{category} ({', '.join(bucket)})" if bucket else category)
    return "; ".join(parts)


def _ask_batch(kind: str, profiles: list[dict], categories: list[str], cfg: dict) -> list[dict]:
    """Call the local Ollama API with a strict response schema."""
    vocab = _category_vocab_line(categories) if categories else ""
    if kind == "merchant":
        instruction = "\n".join(
            [
                "For each statement merchant profile, fill two separate fields:",
                "1) canonical — the consumer-facing brand or merchant name (e.g. Publix, Sheetz, Circle K, Dairy Queen). "
                "When the brand is recognizable from the statement text, you MUST fill canonical. "
                "Never put a category, subcategory, or 'Food/Shopping' style label in canonical. "
                "Leave canonical empty and set ambiguous=true only when the merchant identity is truly unknown.",
                "Payment rails: if the string starts with APLPAY/Apple Pay, SQ/Square, TST, SP, PayPal, or Google Pay, "
                "prefer the underlying merchant after the rail (e.g. ORCA). If only the rail remains, use "
                "'Apple Pay', 'Square', 'PayPal', or 'Google Pay' as canonical — do not leave it blank.",
                "2) category and subcategory — optional personal-finance labels from the allowed vocabulary "
                "(may be empty strings when unsure).",
                GAS_STATION_HINT,
                "Keep the reason focused on how you recognized the brand; mention category only briefly.",
            ]
        )
        category_line = f"Allowed category vocabulary (for category/subcategory only): {vocab}" if vocab else ""
    else:
        instruction = "\n".join(
            [
                "Suggest a personal-finance category and subcategory for each merchant profile.",
                "Use only the allowed category vocabulary.",
                GAS_STATION_HINT,
                "When a merchant could be food or fuel, prefer Transport/Gas for fuel brands and gas stations.",
                "Mark ambiguous true only when the merchant identity itself is unclear.",
            ]
        )
        category_line = f"Allowed category vocabulary: {vocab}" if vocab else ""
    prompt = "\n".join(
        [
            instruction,
            "Return only the JSON schema response.",
            category_line,
            f"Profiles: {_json(profiles)}",
        ]
    ).strip()
    payload = {
        "model": cfg["model"],
        "prompt": prompt,
        "format": _schema(kind),
        "stream": False,
        "think": False,
        "keep_alive": cfg.get("keep_alive", "10m"),
        "options": {
            "temperature": float(cfg.get("temperature", 0)),
            "num_ctx": int(cfg.get("num_ctx", 8192)),
        },
    }
    response = httpx.post(f"{str(cfg['host']).rstrip('/')}/api/generate", json=payload, timeout=300.0)
    response.raise_for_status()
    parsed = json.loads(response.json().get("response") or "{}")
    items = parsed.get("items")
    if not isinstance(items, list):
        raise ValueError("Model response did not contain proposal items")
    return [item for item in items if isinstance(item, dict)]


def _make_proposal(kind: str, profile: dict, result: dict, model: str) -> dict:
    if kind == "merchant":
        recommendation = {
            "canonical": str(result.get("canonical") or "").strip(),
            "category": str(result.get("category") or "").strip(),
            "subcategory": str(result.get("subcategory") or "").strip(),
        }
        fingerprint_input = {"members": profile["members"], "sample_raw": profile["sample_raw"]}
    else:
        recommendation = {
            "category": str(result.get("category") or "").strip(),
            "subcategory": str(result.get("subcategory") or "").strip(),
            "canonical": profile.get("canonical"),
            "reusable": bool(profile.get("history_count", 0) >= 3 and profile.get("history_ratio", 0) >= 0.9),
        }
        fingerprint_input = {
            "key": profile["key"],
            "txn_ids": sorted(profile["txn_ids"]),
            "history": [profile.get("history_category"), profile.get("history_ratio")],
        }
    now = _now()
    return {
        "proposal_id": uuid.uuid4().hex[:16],
        "input_fingerprint": _fingerprint(kind, fingerprint_input),
        "kind": kind,
        "status": "pending",
        "members_json": _json(profile.get("members") or [profile.get("merchant")]),
        "txn_ids_json": _json(profile["txn_ids"]),
        "recommendation_json": _json(recommendation),
        "evidence_json": _json(
            {
                "reason": str(result.get("reason") or ""),
                "ambiguous": bool(result.get("ambiguous")),
                "sample_raw": profile.get("sample_raw", []),
                "txn_count": profile["txn_count"],
                "total_amount": profile["total_amount"],
                "history_category": profile.get("history_category"),
                "history_ratio": profile.get("history_ratio"),
            }
        ),
        "confidence": str(result.get("confidence") or "low").lower() if not result.get("ambiguous") else "low",
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "batch_id": None,
        "created_at": now,
        "updated_at": now,
        "error": None,
    }


def _history_proposal(profile: dict, model: str) -> dict:
    result = {
        "category": profile["history_category"],
        "subcategory": "",
        "confidence": "high",
        "reason": f"{profile['history_count']} reviewed transactions agree {profile['history_ratio']:.0%}.",
        "ambiguous": False,
    }
    return _make_proposal("category", profile, result, model)


def run_analysis(ledger: pd.DataFrame, *, mode: str = "full") -> dict:
    """Create new proposal records without changing the supplied ledger."""
    if ledger.empty:
        return {"error": "No ledger yet. Run ingest first."}
    cfg = recommended_config()
    if not ollama_available(str(cfg["host"])):
        return {"error": f"Ollama is not reachable at {cfg['host']}."}
    normalized = canonicalize(ledger)
    existing = load_proposals()
    active = set(
        existing.loc[existing["status"].isin(["pending", "deferred"]), "input_fingerprint"].dropna().astype(str)
    )
    categories = [c for c in (load_rules().get("categories") or []) if c != "Uncategorized"]
    created: list[dict] = []
    errors: list[str] = []

    merchant_profiles = _profile_unknown_merchants(normalized)
    merchant_pending = []
    for profile in merchant_profiles:
        fingerprint = _fingerprint("merchant", {"members": profile["members"], "sample_raw": profile["sample_raw"]})
        if mode == "incremental" and fingerprint in active:
            continue
        merchant_pending.append(profile)
    for start in range(0, len(merchant_pending), 20):
        batch = merchant_pending[start : start + 20]
        try:
            answers = {str(item.get("key")): item for item in _ask_batch("merchant", batch, categories, cfg)}
            for profile in batch:
                answer = answers.get(profile["key"], {"confidence": "low", "ambiguous": True, "reason": "No model answer."})
                answer = _sanitize_merchant_answer(answer, categories)
                created.append(_make_proposal("merchant", profile, answer, str(cfg["model"])))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"merchant batch {start // 20 + 1}: {exc}")

    category_profiles = _category_profiles(normalized)
    category_pending: list[dict] = []
    for profile in category_profiles:
        fingerprint = _fingerprint(
            "category",
            {"key": profile["key"], "txn_ids": sorted(profile["txn_ids"]), "history": [profile.get("history_category"), profile.get("history_ratio")]},
        )
        if mode == "incremental" and fingerprint in active:
            continue
        if profile.get("history_category") and profile.get("history_ratio", 0) >= 0.9 and profile.get("history_count", 0) >= 3:
            created.append(_history_proposal(profile, str(cfg["model"])))
        else:
            category_pending.append(profile)
    for start in range(0, len(category_pending), 20):
        batch = category_pending[start : start + 20]
        try:
            answers = {str(item.get("key")): item for item in _ask_batch("category", batch, categories, cfg)}
            for profile in batch:
                answer = answers.get(profile["key"], {"confidence": "low", "ambiguous": True, "reason": "No model answer."})
                category = str(answer.get("category") or "")
                if category not in categories:
                    answer = {**answer, "category": "", "confidence": "low", "ambiguous": True}
                created.append(_make_proposal("category", profile, answer, str(cfg["model"])))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"category batch {start // 20 + 1}: {exc}")

    if created:
        # A renewed input supersedes prior pending/deferred proposals for that input.
        fingerprints = {record["input_fingerprint"] for record in created}
        existing = existing[~((existing["input_fingerprint"].isin(fingerprints)) & (existing["status"].isin(["pending", "deferred"])))]
        _write_proposals(pd.concat([existing, pd.DataFrame(created)], ignore_index=True))
    return {
        "merchant_profiles": len(merchant_profiles),
        "category_profiles": len(category_profiles),
        "created": len(created),
        "errors": errors,
    }


def list_proposals(*, status: str | None = "pending", kind: str | None = None, limit: int = 500, offset: int = 0) -> dict:
    frame = load_proposals()
    if status:
        frame = frame[frame["status"] == status]
    if kind:
        frame = frame[frame["kind"] == kind]
    frame = frame.copy()
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    frame["_confidence_rank"] = frame["confidence"].map(confidence_rank).fillna(3)
    frame["_impact"] = frame["evidence_json"].map(
        lambda value: abs(float(_unjson(value, {}).get("total_amount") or 0))
    )
    frame = frame.sort_values(["_confidence_rank", "_impact", "created_at"], ascending=[True, False, False], kind="stable")
    total = int(len(frame))
    rows = []
    for record in frame.iloc[offset : offset + limit].to_dict(orient="records"):
        rows.append(
            {
                **{k: v for k, v in record.items() if not k.endswith("_json")},
                "members": _unjson(record["members_json"], []),
                "txn_ids": _unjson(record["txn_ids_json"], []),
                "recommendation": _unjson(record["recommendation_json"], {}),
                "evidence": _unjson(record["evidence_json"], {}),
            }
        )
    return {"total": total, "items": rows}


def ai_status(*, warmup: bool = False) -> dict:
    cfg = recommended_config()
    host = str(cfg["host"]).rstrip("/")
    result = {"host": host, "model": cfg["model"], "available": False, "model_installed": False, "gpu_resident": False, "size_vram": 0, "message": "Ollama is offline."}
    try:
        tags = httpx.get(f"{host}/api/tags", timeout=3.0).json().get("models", [])
        result["available"] = True
        result["model_installed"] = any(str(item.get("name")) == str(cfg["model"]) for item in tags if isinstance(item, dict))
        if warmup and result["model_installed"]:
            httpx.post(f"{host}/api/generate", json={"model": cfg["model"], "prompt": "Reply with OK.", "stream": False, "think": False, "keep_alive": cfg.get("keep_alive", "10m"), "options": {"num_ctx": 256}}, timeout=90.0).raise_for_status()
        running = httpx.get(f"{host}/api/ps", timeout=5.0).json().get("models", [])
        for item in running:
            if isinstance(item, dict) and str(item.get("name")) == str(cfg["model"]):
                vram = int(item.get("size_vram") or 0)
                result["size_vram"] = vram
                result["gpu_resident"] = vram > 0
        result["message"] = "Local model is ready on the GPU." if result["gpu_resident"] else ("Model is installed. Run a hardware test to confirm GPU residency." if result["model_installed"] else "Install Ollama, then download the recommended model.")
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"Ollama is unavailable: {exc}"
    return result


def pull_model() -> dict:
    cfg = recommended_config()
    host = str(cfg["host"]).rstrip("/")
    try:
        response = httpx.post(f"{host}/api/pull", json={"name": cfg["model"], "stream": False}, timeout=1800.0)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Could not download {cfg['model']}: {exc}"}
    return ai_status(warmup=True)


def _load_applications() -> list[dict]:
    path = _applications_path()
    if not path.exists():
        return []
    return _unjson(path.read_text(encoding="utf-8"), [])


def _write_applications(applications: list[dict]) -> None:
    paths.ensure_dirs()
    atomic_write_text(_applications_path(), _json(applications))


def _snapshot(batch_id: str, ledger_path: Path) -> dict:
    root = _snapshot_root() / batch_id
    root.mkdir(parents=True, exist_ok=False)
    atomic_copy_file(ledger_path, root / "ledger.parquet")
    for name, source in (("merchants.yaml", paths.MERCHANTS_PATH), ("rules.yaml", paths.RULES_PATH)):
        if source.exists():
            atomic_copy_file(source, root / name)
    return {"batch_id": batch_id, "snapshot": str(root), "created_at": _now()}


def apply_decisions(ledger: pd.DataFrame, write_ledger, decisions: list[dict], *, ledger_path: Path) -> dict:
    """Apply approved proposals atomically under the caller's ledger lock."""
    if not decisions:
        return {"error": "Select at least one proposal."}
    proposals = load_proposals()
    selected_ids = {str(item.get("proposal_id")) for item in decisions}
    selected = proposals[(proposals["proposal_id"].isin(selected_ids)) & (proposals["status"].isin(["pending", "deferred"]))].copy()
    if selected.empty:
        return {"error": "No selected pending proposals were found."}
    batch_id = uuid.uuid4().hex[:12]
    snapshot = _snapshot(batch_id, ledger_path)
    out = ledger.copy()
    applied: list[str] = []
    try:
        overrides = {str(item.get("proposal_id")): item for item in decisions}
        for _, proposal in selected.iterrows():
            proposal_id = str(proposal["proposal_id"])
            decision = overrides[proposal_id]
            action = str(decision.get("action") or "accept")
            if action not in {"accept", "reject", "defer"}:
                raise ValueError(f"Unsupported action {action!r}")
            index = proposals["proposal_id"] == proposal_id
            if action != "accept":
                proposals.loc[index, "status"] = "rejected" if action == "reject" else "deferred"
                proposals.loc[index, "updated_at"] = _now()
                continue
            recommendation = _unjson(proposal["recommendation_json"], {})
            if isinstance(decision.get("recommendation"), dict):
                recommendation = {**recommendation, **decision["recommendation"]}
            members = _unjson(proposal["members_json"], [])
            txn_ids = _unjson(proposal["txn_ids_json"], [])
            if proposal["kind"] == "merchant":
                canonical = str(recommendation.get("canonical") or "").strip()
                if not canonical:
                    raise ValueError("Merchant approval requires a canonical name.")
                allowed = [c for c in (load_rules().get("categories") or []) if c != "Uncategorized"]
                if _looks_like_category_label(canonical, allowed):
                    raise ValueError("Canonical brand name cannot be a category label.")
                category = str(recommendation.get("category") or "").strip()
                subcategory = str(recommendation.get("subcategory") or "").strip()
                if category and category not in allowed:
                    raise ValueError(f"Unknown category {category!r}.")
                if not category:
                    subcategory = ""
                append_merchant(
                    canonical=canonical,
                    members=[str(m) for m in members],
                    category=category or None,
                    subcategory=subcategory or None,
                )
                match = out["normalized_merchant"].isin([str(m) for m in members])
                out.loc[match, "canonical_merchant"] = canonical
                out.loc[match, "merchant_source"] = "manual"
                out.loc[match, "proposed_canonical"] = None
                if category:
                    out.loc[match, "category"] = category
                    out.loc[match, "subcategory"] = subcategory
                    out.loc[match, "classified_by"] = "manual"
                    out.loc[match, "proposed_category"] = None
                    out.loc[match, "proposed_subcategory"] = None
            else:
                category = str(recommendation.get("category") or "").strip()
                if not category:
                    raise ValueError("Category approval requires a category.")
                subcategory = str(recommendation.get("subcategory") or "").strip()
                match = out["txn_id"].isin([str(i) for i in txn_ids])
                out.loc[match, "category"] = category
                out.loc[match, "subcategory"] = subcategory
                out.loc[match, "classified_by"] = "manual"
                out.loc[match, "proposed_category"] = None
                out.loc[match, "proposed_subcategory"] = None
                if bool(decision.get("save_as_rule")) and recommendation.get("canonical"):
                    append_rule(merchant_canonical=str(recommendation["canonical"]), category=category, subcategory=subcategory)
            proposals.loc[index, "status"] = "applied"
            proposals.loc[index, "batch_id"] = batch_id
            proposals.loc[index, "updated_at"] = _now()
            applied.append(proposal_id)
        write_ledger(out)
        _write_proposals(proposals)
        applications = _load_applications()
        applications.append({**snapshot, "proposal_ids": applied, "applied_at": _now()})
        _write_applications(applications)
    except Exception:
        # Config edits may have happened before a ledger write error. Restore
        # the full pre-application state so an interrupted batch is all-or-
        # nothing, matching the rest of the pipeline's atomic-write policy.
        root = Path(snapshot["snapshot"])
        prior_ledger = root / "ledger.parquet"
        if prior_ledger.exists():
            atomic_copy_file(prior_ledger, ledger_path)
        for name, target in (("merchants.yaml", paths.MERCHANTS_PATH), ("rules.yaml", paths.RULES_PATH)):
            prior = root / name
            if prior.exists():
                atomic_copy_file(prior, target)
        shutil.rmtree(Path(snapshot["snapshot"]), ignore_errors=True)
        raise
    return {"batch_id": batch_id, "applied": applied, "ledger_rows": int(len(out))}


def rollback_latest(ledger_path: Path) -> dict:
    applications = _load_applications()
    if not applications:
        return {"error": "No AI application is available to roll back."}
    latest = applications[-1]
    root = Path(str(latest.get("snapshot") or ""))
    snapshot_ledger = root / "ledger.parquet"
    if not snapshot_ledger.exists():
        return {"error": "The rollback snapshot is missing."}
    atomic_copy_file(snapshot_ledger, ledger_path)
    for name, target in (("merchants.yaml", paths.MERCHANTS_PATH), ("rules.yaml", paths.RULES_PATH)):
        source = root / name
        if source.exists():
            atomic_copy_file(source, target)
    proposals = load_proposals()
    proposals.loc[proposals["batch_id"] == latest["batch_id"], "status"] = "pending"
    proposals.loc[proposals["batch_id"] == latest["batch_id"], "batch_id"] = None
    proposals.loc[proposals["proposal_id"].isin(latest.get("proposal_ids") or []), "updated_at"] = _now()
    _write_proposals(proposals)
    _write_applications(applications[:-1])
    return {"rolled_back": latest["batch_id"], "proposal_ids": latest.get("proposal_ids") or []}
