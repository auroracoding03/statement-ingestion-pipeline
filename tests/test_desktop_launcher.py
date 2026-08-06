from __future__ import annotations

from unittest.mock import Mock

import src.desktop as desktop


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
