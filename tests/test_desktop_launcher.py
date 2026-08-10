from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import src.desktop as desktop


class _Event:
    def __init__(self) -> None:
        self.callback = None

    def __iadd__(self, callback):
        self.callback = callback
        return self


def test_desktop_launcher_uses_native_webview(monkeypatch) -> None:
    ensure_dirs = Mock()
    server = SimpleNamespace(run=lambda: None, should_exit=False)
    create_window = Mock(return_value=SimpleNamespace(events=SimpleNamespace(closed=_Event())))
    start = Mock()
    fake_webview = SimpleNamespace(create_window=create_window, start=start)
    lock = Mock()

    monkeypatch.setattr(desktop, "ensure_dirs", ensure_dirs)
    monkeypatch.setattr(desktop, "FileLock", lambda _path: lock)
    monkeypatch.setattr(desktop, "_available_port", lambda _port: 8787)
    monkeypatch.setattr(desktop, "_wait_for_server", lambda _url: True)
    monkeypatch.setattr(desktop.uvicorn, "Config", Mock())
    monkeypatch.setattr(desktop.uvicorn, "Server", lambda _config: server)
    monkeypatch.setattr(desktop.threading, "Thread", lambda **_kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    desktop.main()

    ensure_dirs.assert_called_once_with()
    create_window.assert_called_once()
    assert create_window.call_args.args[1] == "http://127.0.0.1:8787"
    start.assert_called_once()
    assert server.should_exit is True
    lock.release.assert_called_once_with()


def test_ensure_stdio_replaces_missing_streams(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    desktop._ensure_stdio()

    assert sys.stdout is not None
    assert sys.stderr is not None
    assert callable(getattr(sys.stdout, "isatty", None))
    assert callable(getattr(sys.stderr, "isatty", None))


def test_second_desktop_instance_shows_native_message(monkeypatch) -> None:
    lock = Mock()
    lock.acquire.side_effect = desktop.Timeout("already running")
    show_error = Mock()

    monkeypatch.setattr(desktop, "ensure_dirs", Mock())
    monkeypatch.setattr(desktop, "FileLock", lambda _path: lock)
    monkeypatch.setattr(desktop, "_show_error", show_error)

    desktop.main()

    show_error.assert_called_once()
    lock.release.assert_not_called()
