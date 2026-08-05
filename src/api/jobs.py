"""In-process job registry for long-running pipeline stages.

The UI kicks off ingest/classify/build and polls for completion rather than
holding a request open for the length of an AI pass.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_MAX_JOBS = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(kind: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "pending",
            "created_at": _now(),
            "finished_at": None,
            "result": None,
            "error": None,
        }
        if len(_JOBS) > _MAX_JOBS:
            oldest = sorted(_JOBS.values(), key=lambda j: j["created_at"])[: len(_JOBS) - _MAX_JOBS]
            for job in oldest:
                _JOBS.pop(job["id"], None)
    return job_id


def run_job(job_id: str, fn: Callable[[], Any]) -> None:
    """Execute a job body, recording success or failure. Safe for BackgroundTasks."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job["status"] = "running"

    try:
        result = fn()
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["status"] = "error" if isinstance(result, dict) and result.get("error") else "done"
                job["result"] = result
                job["error"] = (result or {}).get("error") if isinstance(result, dict) else None
                job["finished_at"] = _now()
    except Exception as exc:  # noqa: BLE001 — surface failures to the UI, never crash the server
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["traceback"] = traceback.format_exc()
                job["finished_at"] = _now()


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs(limit: int = 20) -> list[dict]:
    with _LOCK:
        jobs = sorted(_JOBS.values(), key=lambda j: j["created_at"], reverse=True)
        return [dict(j) for j in jobs[:limit]]
