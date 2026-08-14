"""Local Ollama process helpers."""

from pathlib import Path

import pytest

from src import ai_suggest


def test_start_ollama_serve_skips_spawn_when_online(monkeypatch):
    spawned: list[list[str]] = []
    monkeypatch.setattr(ai_suggest, "ollama_available", lambda host=None: True)
    monkeypatch.setattr(ai_suggest, "_spawn_detached", lambda command: spawned.append(command))

    assert ai_suggest.start_ollama_serve() == {"started": False, "available": True}
    assert spawned == []


def test_start_ollama_serve_missing_binary(monkeypatch):
    monkeypatch.setattr(ai_suggest, "ollama_available", lambda host=None: False)
    monkeypatch.setattr(ai_suggest, "resolve_ollama_binary", lambda: None)

    with pytest.raises(FileNotFoundError, match="not installed"):
        ai_suggest.start_ollama_serve()


def test_start_ollama_serve_polls_until_available(monkeypatch):
    seen = {"n": 0}
    spawned: list[list[str]] = []
    binary = Path("ollama")

    def available(host=None):
        seen["n"] += 1
        return seen["n"] >= 3

    monkeypatch.setattr(ai_suggest, "ollama_available", available)
    monkeypatch.setattr(ai_suggest, "resolve_ollama_binary", lambda: binary)
    monkeypatch.setattr(ai_suggest, "_spawn_detached", lambda command: spawned.append(command))
    monkeypatch.setattr(ai_suggest.time, "sleep", lambda _seconds: None)

    result = ai_suggest.start_ollama_serve(host="http://127.0.0.1:11434", timeout=5, poll_interval=0.01)
    assert result == {"started": True, "available": True}
    assert spawned == [[str(binary), "serve"]]


def test_start_ollama_serve_timeout(monkeypatch):
    times = iter([0.0, 0.1, 0.2, 5.0])
    monkeypatch.setattr(ai_suggest, "ollama_available", lambda host=None: False)
    monkeypatch.setattr(ai_suggest, "resolve_ollama_binary", lambda: Path("ollama"))
    monkeypatch.setattr(ai_suggest, "_spawn_detached", lambda command: None)
    monkeypatch.setattr(ai_suggest.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ai_suggest.time, "monotonic", lambda: next(times, 5.0))

    with pytest.raises(TimeoutError, match="did not become reachable"):
        ai_suggest.start_ollama_serve(timeout=1, poll_interval=0.01)


def test_resolve_ollama_binary_uses_path(monkeypatch, tmp_path: Path):
    exe = tmp_path / "ollama.exe"
    exe.write_text("")
    monkeypatch.setattr(ai_suggest.shutil, "which", lambda _name: str(exe))
    assert ai_suggest.resolve_ollama_binary() == exe


def test_resolve_ollama_binary_windows_fallback(monkeypatch, tmp_path: Path):
    exe = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(ai_suggest.shutil, "which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    assert ai_suggest.resolve_ollama_binary() == exe
