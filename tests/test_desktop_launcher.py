from __future__ import annotations

import sys
from unittest.mock import Mock

import src.desktop as desktop


def test_ensure_stdio_replaces_missing_streams(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    desktop._ensure_stdio()

    assert sys.stdout is not None
    assert sys.stderr is not None
    assert callable(getattr(sys.stdout, "isatty", None))
    assert callable(getattr(sys.stderr, "isatty", None))


def test_ensure_stdio_allows_uvicorn_logging_config(monkeypatch) -> None:
    """Reproduce the PyInstaller windowed crash path and prove the fix."""
    from logging.config import dictConfig

    from uvicorn.config import LOGGING_CONFIG

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    desktop._ensure_stdio()

    dictConfig(LOGGING_CONFIG)


def test_desktop_launcher_opens_existing_server(monkeypatch) -> None:
    open_browser = Mock()
    monkeypatch.setattr(desktop, "_server_is_running", lambda _port: True)
    monkeypatch.setattr(desktop.webbrowser, "open", open_browser)

    desktop.main()

    open_browser.assert_called_once_with("http://127.0.0.1:8787")


def test_desktop_launcher_initializes_and_starts_local_server(monkeypatch) -> None:
    ensure_dirs = Mock()
    open_browser = Mock()
    uvicorn_run = Mock()
    timer = Mock()
    timer.start = Mock()

    monkeypatch.setattr(desktop, "_server_is_running", lambda _port: False)
    monkeypatch.setattr(desktop, "ensure_dirs", ensure_dirs)
    monkeypatch.setattr(desktop.webbrowser, "open", open_browser)
    monkeypatch.setattr(desktop.uvicorn, "run", uvicorn_run)
    monkeypatch.setattr(desktop.threading, "Timer", lambda *_args: timer)
    monkeypatch.setattr(desktop, "_available_port", lambda _port: 8787)

    desktop.main()

    ensure_dirs.assert_called_once_with()
    timer.start.assert_called_once_with()
    uvicorn_run.assert_called_once_with(
        desktop.app, host="127.0.0.1", port=8787, log_level="warning"
    )
