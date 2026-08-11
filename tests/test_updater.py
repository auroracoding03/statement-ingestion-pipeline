from __future__ import annotations

import json
import subprocess
import sys
import time
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


def test_launch_installer_uses_bounded_handoff_before_relaunch(tmp_path: Path, monkeypatch) -> None:
    installer = Path(
        r"C:\Users\Ada Lovelace\AppData\Local\Statement Pipeline\updates\StatementPipelineSetup-0.2.6.exe"
    )
    installed_exe = Path(
        r"C:\Users\Ada Lovelace\AppData\Local\Programs\Statement Pipeline\StatementPipeline.exe"
    )
    monkeypatch.setattr(updater, "USER_DATA_ROOT", tmp_path)
    spawn = Mock()

    monkeypatch.setattr(updater, "_installed_executable", lambda: installed_exe)
    monkeypatch.setattr(updater, "_spawn_detached", spawn)

    updater._launch_installer(installer)

    command = spawn.call_args.args[0]
    assert command[:4] == ["cmd.exe", "/c", "start", ""]
    assert command[4] == "/MIN"
    assert command[5:10] == [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ]
    script_path = Path(command[10])
    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8-sig")
    assert "Get-Process -Name" in script
    assert "StatementPipeline" in script
    assert "force-stop" in script
    assert "taskkill" in script
    assert "Stop-Process -Id $targetId -Force" in script
    assert "lock-cleared" in script
    assert "statement-pipeline.desktop.lock" in script
    assert "install-start" in script
    assert "relaunch-delegated-to-installer" in script
    assert "relaunch-ok" not in script
    assert "/VERYSILENT" in script
    assert json.dumps(str(installer)) in script
    assert json.dumps(str(tmp_path / "updates" / "update.log")) in script
    assert (tmp_path / "updates" / "update.log").exists()


def test_spawn_detached_retries_without_breakaway_on_access_denied(monkeypatch) -> None:
    calls: list[int] = []

    def fake_popen(_command, **kwargs):
        flags = int(kwargs.get("creationflags") or 0)
        calls.append(flags)
        if flags & updater.CREATE_BREAKAWAY_FROM_JOB:
            err = PermissionError(13, "Access is denied")
            err.winerror = 5  # type: ignore[attr-defined]
            raise err
        return Mock(name="proc")

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(updater, "_write_update_progress", lambda _msg: None)

    proc = updater._spawn_detached(["cmd.exe", "/c", "echo", "ok"])

    assert proc is not None
    assert len(calls) == 2
    assert calls[0] & updater.CREATE_BREAKAWAY_FROM_JOB
    assert not (calls[1] & updater.CREATE_BREAKAWAY_FROM_JOB)


def test_force_exit_releases_desktop_lock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(updater, "USER_DATA_ROOT", tmp_path)
    lock = tmp_path / updater.DESKTOP_LOCK_NAME
    lock.write_text("", encoding="utf-8")
    release = Mock()
    spawn = Mock()
    exits: list[int] = []

    import src.desktop as desktop

    monkeypatch.setattr(desktop, "release_instance_lock", release)
    monkeypatch.setattr(updater, "_spawn_detached", spawn)
    monkeypatch.setattr(updater.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(updater.os, "_exit", lambda code: exits.append(code))

    updater._force_exit_process_tree()

    release.assert_called_once_with()
    assert not lock.exists()
    assert exits == [0]
    assert spawn.call_count >= 1


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
    exit_calls: list[int] = []
    monkeypatch.setattr(updater, "_download", download)
    monkeypatch.setattr(
        updater,
        "_expected_checksum",
        lambda _url: updater._file_checksum(
            tmp_path / "updates" / f"StatementPipelineSetup-{FUTURE_VERSION}.exe"
        ),
    )
    monkeypatch.setattr(updater, "_launch_installer", launch)
    monkeypatch.setattr(updater.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(updater, "_force_exit_process_tree", lambda: exit_calls.append(0))
    monkeypatch.setattr(updater, "_start_install_worker", updater._install_worker)

    result = updater.install_latest_update()

    assert "restart" in result["message"]
    launch.assert_called_once_with(
        tmp_path / "updates" / f"StatementPipelineSetup-{FUTURE_VERSION}.exe"
    )
    assert exit_calls == [0]


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
    monkeypatch.setattr(updater, "_start_install_worker", updater._install_worker)

    result = updater.install_latest_update()
    assert "restart" in result["message"]

    assert not (tmp_path / "updates" / f"StatementPipelineSetup-{FUTURE_VERSION}.exe").exists()
    launch.assert_not_called()
    assert "checksum-mismatch" in (tmp_path / "updates" / "update.log").read_text(encoding="utf-8")


def test_install_worker_surfaces_handoff_spawn_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(updater, "USER_DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        updater,
        "_download",
        lambda _url, destination: destination.write_bytes(b"installer"),
    )
    monkeypatch.setattr(updater, "_expected_checksum", lambda _url: updater._file_checksum(
        tmp_path / "updates" / f"StatementPipelineSetup-{FUTURE_VERSION}.exe"
    ))
    monkeypatch.setattr(
        updater,
        "_launch_installer",
        Mock(side_effect=PermissionError(13, "Access is denied")),
    )
    shown: list[str] = []
    monkeypatch.setattr(updater, "_show_update_error", lambda message: shown.append(message))
    monkeypatch.setattr(updater, "_force_exit_process_tree", Mock())

    updater._install_worker(
        {
            "latest_version": FUTURE_VERSION,
            "installer_url": "https://example.test/installer.exe",
            "checksum_url": "https://example.test/installer.sha256",
            "release_url": "https://example.test/release",
        }
    )

    log_text = (tmp_path / "updates" / "update.log").read_text(encoding="utf-8")
    assert "worker-error PermissionError" in log_text
    assert shown and "handoff" in shown[0].lower()
    updater._force_exit_process_tree.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows update handoff")
def test_handoff_force_stops_snapshot_survivor_before_install(tmp_path: Path) -> None:
    """Bounded handoff must terminate a live snapshot survivor before install."""
    import shutil

    probe_exe = tmp_path / "HandoffProbeSurvivor.exe"
    shutil.copy2(sys.executable, probe_exe)
    sleeper = subprocess.Popen(
        [str(probe_exe), "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    installer = tmp_path / "fake-installer.cmd"
    installer.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    relaunch = Path(r"C:\Windows\System32\where.exe")
    log_path = tmp_path / "update.log"
    lock_path = tmp_path / "statement-pipeline.desktop.lock"
    lock_path.write_text("", encoding="utf-8")

    try:
        command = updater._installer_command(
            installer,
            relaunch,
            log_path=log_path,
            lock_path=lock_path,
            process_name=probe_exe.stem,
            graceful_seconds=1,
            force_wait_seconds=10,
        )
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert sleeper.poll() is not None
        log_text = log_path.read_text(encoding="utf-8")
        assert "handoff-start" in log_text
        assert "force-stop" in log_text
        assert "lock-cleared" in log_text
        assert "install-ok" in log_text
        assert "relaunch-delegated-to-installer" in log_text
        assert "handoff-error" not in log_text
        assert not lock_path.exists()
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=5)
        time.sleep(0.2)
