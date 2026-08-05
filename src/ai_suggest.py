"""Ollama-assisted category *proposals* for unclassified transactions.

Proposals are never final — `fin review` must confirm before a rule is written.
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
from src.paths import OLLAMA_PATH, RULES_PATH

console = Console()


def load_ollama_config(path: Path = OLLAMA_PATH) -> dict:
    if not path.exists():
        return {"model": "llama3.2", "host": "http://127.0.0.1:11434", "temperature": 0.1}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def ollama_available(host: str) -> bool:
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _parse_json_payload(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def suggest_one(
    *,
    merchant: str,
    amount: float,
    categories: list[str],
    model: str,
    host: str,
    temperature: float,
) -> tuple[str, str]:
    prompt = (
        "You classify personal finance transactions.\n"
        f"Allowed categories: {', '.join(categories)}\n"
        "Respond with ONLY compact JSON: "
        '{"category":"...","subcategory":"..."}\n'
        f"Merchant: {merchant}\n"
        f"Amount: {amount:.2f}\n"
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": temperature},
    }
    r = httpx.post(f"{host.rstrip('/')}/api/generate", json=payload, timeout=120.0)
    r.raise_for_status()
    data = r.json()
    parsed = _parse_json_payload(data.get("response", "{}"))
    category = str(parsed.get("category") or "Uncategorized")
    subcategory = str(parsed.get("subcategory") or "")
    if category not in categories:
        category = "Uncategorized"
    return category, subcategory


def suggest(frame: pd.DataFrame, rules_path: Path = RULES_PATH) -> pd.DataFrame:
    out = frame.copy()
    if "proposed_category" not in out.columns:
        out["proposed_category"] = None
        out["proposed_subcategory"] = None

    cfg = load_ollama_config()
    host = cfg.get("host", "http://127.0.0.1:11434")
    model = cfg.get("model", "llama3.2")
    temperature = float(cfg.get("temperature", 0.1))

    mask = out["category"].isna() | (out["category"] == "") | (out["category"] == "Uncategorized")
    # Don't overwrite already-rule-classified rows
    mask = out["classified_by"].isna() | (out["classified_by"] == "")
    pending = out[mask]
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
                merchant=str(row["normalized_merchant"]),
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
            console.print(f"[red]AI failed[/red] {row['normalized_merchant']}: {exc}")

    return out
