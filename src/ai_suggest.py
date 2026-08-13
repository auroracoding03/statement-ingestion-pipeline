"""Ollama-assisted proposals for the unclassified tail.

Two jobs, both advisory only:
  suggest()               category / subcategory for open transactions
  suggest_canonical_name()  a brand name for a fuzzy merchant cluster

Nothing here is authoritative. A human confirms via `fin review` or the UI
before anything is written into rules.yaml or merchants.yaml.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pandas as pd
import yaml
from rich.console import Console

from src.classify import load_rules
from src.paths import OLLAMA_PATH

console = Console()

DEFAULT_CONFIG = {
    "model": "qwen3.5:9b",
    "host": "http://127.0.0.1:11434",
    "temperature": 0,
    "num_ctx": 8192,
    "keep_alive": "10m",
}

RECOMMENDED_MODEL = "qwen3.5:9b"


def load_ollama_config(path: Path = OLLAMA_PATH) -> dict:
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    with path.open() as f:
        loaded = yaml.safe_load(f) or {}
    return {**DEFAULT_CONFIG, **loaded}


def recommended_config(path: Path = OLLAMA_PATH) -> dict:
    """Map the legacy llama3.2 default to the current local model.

    Installed desktops keep an old ollama.yaml from earlier releases.  That
    value was the application's historical default, not a deliberate choice.
    """
    cfg = load_ollama_config(path)
    if str(cfg.get("model") or "") == "llama3.2":
        cfg["model"] = RECOMMENDED_MODEL
        cfg["temperature"] = 0
        cfg["num_ctx"] = 8192
        cfg["keep_alive"] = "10m"
    return cfg


def ollama_available(host: str | None = None) -> bool:
    host = host or load_ollama_config()["host"]
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _parse_json_payload(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _generate(prompt: str, *, model: str, host: str, temperature: float) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": temperature},
    }
    r = httpx.post(f"{host.rstrip('/')}/api/generate", json=payload, timeout=120.0)
    r.raise_for_status()
    return _parse_json_payload(r.json().get("response", "{}"))


def suggest_one(
    *,
    raw_description: str,
    normalized_merchant: str,
    canonical_merchant: str | None,
    amount: float,
    categories: list[str],
    model: str,
    host: str,
    temperature: float,
) -> tuple[str, str]:
    """Propose a category using whichever merchant name form is most legible."""
    lines = [
        "You classify personal finance transactions.",
        f"Allowed categories: {', '.join(categories)}",
        'Respond with ONLY compact JSON: {"category":"...","subcategory":"..."}',
        f"Raw: {raw_description}",
        f"Normalized: {normalized_merchant}",
    ]
    if canonical_merchant:
        lines.append(f"Canonical: {canonical_merchant}")
    lines.append(f"Amount: {amount:.2f}")

    parsed = _generate("\n".join(lines), model=model, host=host, temperature=temperature)
    category = str(parsed.get("category") or "Uncategorized")
    subcategory = str(parsed.get("subcategory") or "")
    if category not in categories:
        category = "Uncategorized"
    return category, subcategory


def suggest_canonical_name(
    *,
    members: list[str],
    sample_raw: str = "",
    model: str | None = None,
    host: str | None = None,
    temperature: float | None = None,
) -> str | None:
    """Propose a human-readable brand name for a cluster of merchant variants."""
    cfg = load_ollama_config()
    model = model or cfg["model"]
    host = host or cfg["host"]
    temperature = cfg["temperature"] if temperature is None else temperature

    prompt = "\n".join(
        [
            "These credit card statement merchant strings refer to the same business.",
            "Identify the real-world brand name.",
            'Respond with ONLY compact JSON: {"canonical":"..."}',
            "Use the common consumer-facing brand name, correctly capitalized.",
            f"Variants: {', '.join(members[:12])}",
            f"Sample raw line: {sample_raw}" if sample_raw else "",
        ]
    ).strip()

    try:
        parsed = _generate(prompt, model=model, host=host, temperature=temperature)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]AI canonical-name failed[/red] {members[:1]}: {exc}")
        return None

    canonical = str(parsed.get("canonical") or "").strip()
    return canonical or None


def propose_canonicals_for_clusters(clusters: list[dict]) -> list[dict]:
    """Fill `proposed_canonical` on each cluster; a no-op if Ollama is down."""
    if not clusters:
        return clusters
    cfg = load_ollama_config()
    if not ollama_available(cfg["host"]):
        console.print(f"[yellow]Ollama not reachable at {cfg['host']}; skipping name proposals.[/yellow]")
        return clusters

    for cluster in clusters:
        if cluster.get("proposed_canonical"):
            continue
        cluster["proposed_canonical"] = suggest_canonical_name(
            members=cluster.get("members") or [],
            sample_raw=cluster.get("sample_raw") or "",
            model=cfg["model"],
            host=cfg["host"],
            temperature=cfg["temperature"],
        )
    return clusters


def _needs_suggestion(row: pd.Series) -> bool:
    by = row.get("classified_by")
    if by in ("rule", "manual", "merchant"):
        return False
    category = row.get("category")
    return category is None or category == "" or category == "Uncategorized" or pd.isna(category)


def suggest(frame: pd.DataFrame, rules_path: Path | None = None) -> pd.DataFrame:
    out = frame.copy()
    for column in ("proposed_category", "proposed_subcategory"):
        if column not in out.columns:
            out[column] = None
    if out.empty:
        return out

    cfg = load_ollama_config()
    host, model, temperature = cfg["host"], cfg["model"], float(cfg["temperature"])

    pending = out[out.apply(_needs_suggestion, axis=1)]
    if pending.empty:
        console.print("[green]Nothing left for AI suggestions.[/green]")
        return out

    if not ollama_available(host):
        console.print(
            f"[yellow]Ollama not reachable at {host}. Skipping AI suggestions.[/yellow]\n"
            "Start Ollama and re-run: fin classify --with-ai"
        )
        return out

    categories = [c for c in (load_rules(rules_path).get("categories") or []) if c != "Uncategorized"]
    console.print(f"[cyan]Suggesting categories for {len(pending)} transactions via {model}…[/cyan]")

    for idx, row in pending.iterrows():
        try:
            cat, sub = suggest_one(
                raw_description=str(row.get("raw_description") or ""),
                normalized_merchant=str(row.get("normalized_merchant") or ""),
                canonical_merchant=(str(row["canonical_merchant"]) if row.get("canonical_merchant") else None),
                amount=float(row["amount"]),
                categories=categories,
                model=model,
                host=host,
                temperature=temperature,
            )
            out.at[idx, "proposed_category"] = cat
            out.at[idx, "proposed_subcategory"] = sub
            out.at[idx, "classified_by"] = "ai"
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]AI failed[/red] {row.get('normalized_merchant')}: {exc}")

    return out
