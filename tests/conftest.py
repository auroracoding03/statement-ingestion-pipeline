"""Global test safety net.

An earlier iteration of the config loaders bound their paths as import-time
default arguments, so a test that monkeypatched the module attribute still wrote
into the real `config/`. These fixtures make that class of leak fail loudly
instead of silently mutating the repository.
"""

from __future__ import annotations

import pytest

import src.paths as paths


@pytest.fixture(autouse=True)
def guard_real_config(tmp_path, monkeypatch, request):
    """Point config writes at a temp copy unless a test opts out.

    Tests that need the genuine config can request the `real_config` marker.
    """
    if request.node.get_closest_marker("real_config"):
        return

    config = tmp_path / "_guard_config"
    config.mkdir(exist_ok=True)

    for name in ("RULES_PATH", "MERCHANTS_PATH", "EXPECTED_RECURRING_PATH", "PUBLISH_PATH"):
        original = getattr(paths, name)
        target = config / original.name
        if original.exists():
            target.write_text(original.read_text())
        monkeypatch.setattr(paths, name, target)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_config: allow the test to read the repository's real config files"
    )
