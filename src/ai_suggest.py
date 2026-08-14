"""Ollama-assisted proposals for the unclassified tail.

Two jobs, both advisory only:
  suggest()               category / subcategory for open transactions
  suggest_canonical_name()  a brand name for a fuzzy merchant cluster

Nothing here is authoritative. A human confirms via `fin review` or the UI
before anything is written into rules.yaml or merchants.yaml.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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
OLLAMA_START_TIMEOUT_SECONDS = 20.0
OLLAMA_START_POLL_SECONDS = 0.25
OLLAMA_WINDOWS_INSTALL_URL = "https://ollama.com/download/windows"
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
_SHIM_SUFFIXES = {".cmd", ".bat", ".ps1"}


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


def resolve_ollama_binary() -> Path | None:
    """Locate the Ollama CLI, preferring a real .exe over PATHEXT shims."""
    def _usable(path: Path | None) -> Path | None:
        if path is None:
            return None
        if path.suffix.lower() in _SHIM_SUFFIXES:
            sibling = path.with_suffix(".exe")
            return sibling if sibling.is_file() else None
        return path

    found_exe = shutil.which("ollama.exe")
    resolved = _usable(Path(found_exe) if found_exe else None)
    if resolved is not None:
        return resolved
    found = shutil.which("ollama")
    resolved = _usable(Path(found) if found else None)
    if resolved is not None:
        return resolved
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("ProgramFiles") or os.environ.get("PROGRAMFILES")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe")
    if program_files:
        candidates.append(Path(program_files) / "Ollama" / "ollama.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _is_access_denied(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "winerror", None) == 5:
        return True
    return isinstance(exc, OSError) and getattr(exc, "errno", None) in {13, 5}


def _hide_console_startupinfo() -> Any:
    startupinfo = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo is None:
        return None
    info = startupinfo()
    info.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 1))
    info.wShowWindow = 0
    return info


def _detach_creationflags(*, breakaway: bool = True) -> int:
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW) or CREATE_NO_WINDOW) | int(
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    if breakaway:
        flags |= CREATE_BREAKAWAY_FROM_JOB
    return flags


def _spawn_detached(command: list[str]) -> subprocess.Popen[Any]:
    """Start Ollama so it outlives this API request (and the WebView host)."""
    common: dict[str, Any] = {
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform != "win32":
        return subprocess.Popen(command, start_new_session=True, **common)
    info = _hide_console_startupinfo()
    if info is not None:
        common["startupinfo"] = info
    try:
        return subprocess.Popen(
            command,
            creationflags=_detach_creationflags(breakaway=True),
            **common,
        )
    except OSError as exc:
        if not _is_access_denied(exc):
            raise
        return subprocess.Popen(
            command,
            creationflags=_detach_creationflags(breakaway=False),
            **common,
        )


def start_ollama_serve(
    *,
    host: str | None = None,
    timeout: float = OLLAMA_START_TIMEOUT_SECONDS,
    poll_interval: float = OLLAMA_START_POLL_SECONDS,
) -> dict:
    """Start the local Ollama daemon if it is not already reachable.

    Returns ``{"started": bool, "available": bool}``. Raises ``FileNotFoundError``
    when the binary is missing and ``TimeoutError`` if the HTTP API never comes up.
    """
    host = host or str(load_ollama_config()["host"])
    if ollama_available(host):
        return {"started": False, "available": True}

    binary = resolve_ollama_binary()
    if binary is None:
        raise FileNotFoundError(
            "Ollama is not installed. Install Ollama for Windows from "
            f"{OLLAMA_WINDOWS_INSTALL_URL}, then try again."
        )

    _spawn_detached([str(binary), "serve"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ollama_available(host):
            return {"started": True, "available": True}
        time.sleep(poll_interval)
    raise TimeoutError(
        "Ollama did not become reachable. Check that it is installed and try again."
    )


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
