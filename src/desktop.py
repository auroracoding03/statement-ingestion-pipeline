"""Entry point for the installed Windows desktop application."""

from __future__ import annotations

import os
import socket
import threading
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from src.api.app import app
from src.paths import ensure_dirs


HOST = "127.0.0.1"
PORT = int(os.environ.get("STATEMENT_PIPELINE_PORT", "8787"))


def _server_is_running(port: int) -> bool:
    """Return true only when the listener is another Statement Pipeline app."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/status", timeout=0.35):
            return True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _available_port(preferred_port: int) -> int:
    """Prefer the stable port, but avoid an unrelated app already using it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, preferred_port))
            return preferred_port
        except OSError:
            probe.bind((HOST, 0))
            return int(probe.getsockname()[1])


def main() -> None:
    """Open the existing app, or start one local-only server and open it."""
    if _server_is_running(PORT):
        webbrowser.open(f"http://{HOST}:{PORT}")
        return

    port = _available_port(PORT)
    url = f"http://{HOST}:{port}"
    ensure_dirs()
    threading.Timer(0.9, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
