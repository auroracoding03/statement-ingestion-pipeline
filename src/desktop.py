"""Entry point for the installed native Windows desktop application."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
from filelock import FileLock, Timeout

from src.api.app import app
from src.paths import ASSET_ROOT, USER_DATA_ROOT, ensure_dirs


HOST = "127.0.0.1"
PREFERRED_PORT = 8787
STARTUP_TIMEOUT_SECONDS = 12


def _ensure_stdio() -> None:
    """Give windowed PyInstaller builds usable logging streams.

    PyInstaller's ``console=False`` mode leaves the standard output streams as
    ``None`` on Windows. Uvicorn inspects them while configuring logging, so
    replace them before constructing the server.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _available_port(preferred_port: int) -> int:
    """Prefer a stable port without colliding with another local program."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, preferred_port))
            return preferred_port
        except OSError:
            probe.bind((HOST, 0))
            return int(probe.getsockname()[1])


def _server_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/status", timeout=0.35):
            return True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _wait_for_server(url: str) -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _server_ready(url):
            return True
        time.sleep(0.1)
    return False


def _show_error(title: str, message: str) -> None:
    """Show a native error without falling back to a browser window."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:  # noqa: BLE001 — desktop UI may not be available during startup
        pass


def main() -> None:
    """Run FastAPI in the background and render it in a native WebView window."""
    _ensure_stdio()
    ensure_dirs()
    instance_lock = FileLock(str(USER_DATA_ROOT / "statement-pipeline.desktop.lock"))
    try:
        instance_lock.acquire(timeout=0)
    except Timeout:
        _show_error("Statement Pipeline", "Statement Pipeline is already running.")
        return

    server: uvicorn.Server | None = None
    try:
        port = _available_port(PREFERRED_PORT)
        url = f"http://{HOST}:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, name="statement-pipeline-api", daemon=True)
        thread.start()
        if not _wait_for_server(url):
            _show_error("Statement Pipeline", "The local application service did not start.")
            return

        # Imported only for packaged desktop runs; developer commands keep using
        # `fin serve` and a regular browser.
        import webview

        icon = ASSET_ROOT / "packaging" / "assets" / "statement-pipeline.ico"
        window = webview.create_window(
            "Statement Pipeline",
            url,
            width=1440,
            height=960,
            min_size=(980, 700),
        )
        window.events.closed += lambda: setattr(server, "should_exit", True)
        webview.start(icon=str(icon) if icon.exists() else None)
    except Exception as exc:  # noqa: BLE001 — surface startup failures in a native dialog
        _show_error("Statement Pipeline", f"The application could not start.\n\n{exc}")
    finally:
        if server is not None:
            server.should_exit = True
        instance_lock.release()


if __name__ == "__main__":
    main()
