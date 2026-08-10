from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

import src.updater as updater


FUTURE_VERSION = "99.0.0"


def test_check_for_update_reports_unsupported_outside_installed_windows(monkeypatch) -> None:
    monkeypatch.setattr(updater, "_windows_desktop_build", lambda: False)

    result = updater.check_for_update()

    assert result["supported"] is False
    assert result["update_available"] is False


def test_check_for_update_reports_newer_release(monkeypatch) -> None:
    monkeypatch.setattr(updater, "_windows_desktop_build", lambda: True)
    monkeypatch.setattr(
        updater,
        "_latest_release",
        lambda: {
                "latest_version": FUTURE_VERSION,
            "installer_url": "https://example.test/installer.exe",
            "checksum_url": "https://example.test/installer.sha256",
            "release_url": "https://example.test/release",
        },
    )

    result = updater.check_for_update()

    assert result["supported"] is True
    assert result["update_available"] is True
    assert result["latest_version"] == FUTURE_VERSION


def test_install_update_verifies_checksum_before_launching(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(updater, "_windows_desktop_build", lambda: True)
    monkeypatch.setattr(updater, "USER_DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        updater,
        "_latest_release",
        lambda: {
                "latest_version": FUTURE_VERSION,
            "installer_url": "https://example.test/installer.exe",
            "checksum_url": "https://example.test/installer.sha256",
            "release_url": "https://example.test/release",
        },
    )

    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(b"verified installer")

    launch = Mock()
    monkeypatch.setattr(updater, "_download", download)
    monkeypatch.setattr(updater, "_expected_checksum", lambda _url: updater._file_checksum(tmp_path / "updates" / f"StatementPipelineSetup-{FUTURE_VERSION}.exe"))
    monkeypatch.setattr(updater, "_launch_installer", launch)

    result = updater.install_latest_update()

    assert "restart" in result["message"]
    launch.assert_called_once_with(tmp_path / "updates" / f"StatementPipelineSetup-{FUTURE_VERSION}.exe")


def test_install_update_rejects_bad_checksum(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(updater, "_windows_desktop_build", lambda: True)
    monkeypatch.setattr(updater, "USER_DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        updater,
        "_latest_release",
        lambda: {
                "latest_version": FUTURE_VERSION,
            "installer_url": "https://example.test/installer.exe",
            "checksum_url": "https://example.test/installer.sha256",
            "release_url": "https://example.test/release",
        },
    )
    monkeypatch.setattr(updater, "_download", lambda _url, destination: destination.write_bytes(b"installer"))
    monkeypatch.setattr(updater, "_expected_checksum", lambda _url: "0" * 64)
    launch = Mock()
    monkeypatch.setattr(updater, "_launch_installer", launch)

    with pytest.raises(updater.UpdateError, match="checksum did not match"):
        updater.install_latest_update()

    assert not (tmp_path / "updates" / f"StatementPipelineSetup-{FUTURE_VERSION}.exe").exists()
    launch.assert_not_called()
