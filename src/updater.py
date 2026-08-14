"""Safe in-place updates for the installed Windows application."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from src.paths import USER_DATA_ROOT
from src.version import APP_VERSION, INSTALLER_NAME, RELEASE_REPOSITORY


GITHUB_API = f"https://api.github.com/repos/{RELEASE_REPOSITORY}/releases/latest"
CHECKSUM_NAME = f"{INSTALLER_NAME}.sha256"
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
PROCESS_NAME = "StatementPipeline"
DESKTOP_LOCK_NAME = "statement-pipeline.desktop.lock"
GRACEFUL_EXIT_SECONDS = 8
FORCE_STOP_WAIT_SECONDS = 15
NETWORK_TIMEOUT_SECONDS = 30
# Survive parent process/job teardown when the frozen WebView host exits.
# Jobs that disallow breakaway reject this flag with WinError 5 — callers fall back.
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


class UpdateError(RuntimeError):
    """A user-safe update failure that can be shown by the local API."""


def _windows_desktop_build() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _hide_console_startupinfo() -> Any:
    startupinfo = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo is None:
        return None
    info = startupinfo()
    info.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 1))
    info.wShowWindow = 0
    return info


def _detach_creationflags(*, breakaway: bool = True) -> int:
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW) or CREATE_NO_WINDOW) | int(
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    if breakaway:
        flags |= CREATE_BREAKAWAY_FROM_JOB
    return flags


def _is_access_denied(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    winerror = getattr(exc, "winerror", None)
    if winerror == 5:
        return True
    return isinstance(exc, OSError) and getattr(exc, "errno", None) in {13, 5}


def _spawn_detached(command: list[str]) -> subprocess.Popen[Any]:
    """Start a process that should outlive this WebView host.

    Prefer job breakaway; if the host job forbids it (Access Denied), retry without
    the breakaway flag so the handoff can still start.
    """
    common: dict[str, Any] = {
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        info = _hide_console_startupinfo()
        if info is not None:
            common["startupinfo"] = info
    try:
        return subprocess.Popen(
            command,
            creationflags=_detach_creationflags(breakaway=True),
            **common,
        )
    except OSError as exc:
        if not _is_access_denied(exc):
            raise
        _write_update_progress("spawn-breakaway-denied; retrying without breakaway")
        return subprocess.Popen(
            command,
            creationflags=_detach_creationflags(breakaway=False),
            **common,
        )


def _version_key(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise UpdateError(f"Unsupported release version: {value!r}")
    return tuple(int(part) for part in match.groups())


def _update_log_path() -> Path:
    return USER_DATA_ROOT / "updates" / "update.log"


def _desktop_lock_path() -> Path:
    return USER_DATA_ROOT / DESKTOP_LOCK_NAME


def _write_update_progress(message: str) -> None:
    """Append a Python-side progress line before the PowerShell handoff exists."""
    path = _update_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def _show_update_error(message: str) -> None:
    """Surface a hard update failure even when the API already returned."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "Statement Pipeline Update", 0x10)
    except Exception:  # noqa: BLE001 — desktop UI may be unavailable
        pass


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "StatementPipeline"},
    )
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise UpdateError("Could not check GitHub for updates. Check your internet connection.") from exc
    if not isinstance(payload, dict):
        raise UpdateError("GitHub returned an invalid release response.")
    return payload


def _latest_release() -> dict[str, Any]:
    payload = _request_json(GITHUB_API)
    tag = str(payload.get("tag_name") or "")
    latest_version = tag.removeprefix("v")
    _version_key(latest_version)
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("The latest release has no downloadable installer.")

    by_name = {str(asset.get("name")): asset for asset in assets if isinstance(asset, dict)}
    installer = by_name.get(INSTALLER_NAME)
    checksum = by_name.get(CHECKSUM_NAME)
    if not installer or not checksum:
        raise UpdateError("The latest release is missing its installer or checksum.")

    installer_url = installer.get("browser_download_url")
    checksum_url = checksum.get("browser_download_url")
    if not isinstance(installer_url, str) or not isinstance(checksum_url, str):
        raise UpdateError("The latest release has invalid download links.")
    return {
        "latest_version": latest_version,
        "installer_url": installer_url,
        "checksum_url": checksum_url,
        "release_url": str(payload.get("html_url") or ""),
    }


def check_for_update() -> dict[str, Any]:
    """Report whether GitHub Releases has a newer compatible installer."""
    if not _windows_desktop_build():
        return {
            "supported": False,
            "current_version": APP_VERSION,
            "update_available": False,
            "message": "Updates are available from the installed Windows application.",
        }

    release = _latest_release()
    update_available = _version_key(release["latest_version"]) > _version_key(APP_VERSION)
    return {
        "supported": True,
        "current_version": APP_VERSION,
        "update_available": update_available,
        "latest_version": release["latest_version"],
        "release_url": release["release_url"],
        "message": (
            f"Version {release['latest_version']} is ready to install."
            if update_available
            else "You are using the latest version."
        ),
    }


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "StatementPipeline"})
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response, destination.open(
            "wb"
        ) as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise UpdateError("The update download failed. Your installed version was not changed.") from exc


def _expected_checksum(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "StatementPipeline"})
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            text = response.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UpdateError("Could not verify the update checksum.") from exc
    match = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    if not match:
        raise UpdateError("The update checksum file is invalid.")
    return match.group(1).lower()


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _installed_executable() -> Path:
    return Path(sys.executable).resolve()


def _handoff_script_text(
    installer: Path,
    installed_exe: Path,
    *,
    log_path: Path,
    lock_path: Path,
    process_name: str = PROCESS_NAME,
    graceful_seconds: int = GRACEFUL_EXIT_SECONDS,
    force_wait_seconds: int = FORCE_STOP_WAIT_SECONDS,
) -> str:
    """PowerShell that waits for the app to exit, force-kills leftovers, then runs Setup."""
    installer_literal = json.dumps(str(installer))
    exe_literal = json.dumps(str(installed_exe))
    log_literal = json.dumps(str(log_path))
    lock_literal = json.dumps(str(lock_path))
    process_literal = json.dumps(process_name)
    image_literal = json.dumps(f"{process_name}.exe")
    return f"""$ErrorActionPreference = 'Stop'
$logPath = {log_literal}
$lockPath = {lock_literal}
function Write-UpdateLog([string]$Message) {{
  $line = '{{0:u}} {{1}}' -f (Get-Date).ToUniversalTime(), $Message
  Add-Content -LiteralPath $logPath -Value $line
}}
try {{
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null
  Write-UpdateLog 'handoff-start'
  Write-UpdateLog ('target-exe ' + {exe_literal})
  $targets = @(Get-Process -Name {process_literal} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
  Write-UpdateLog ('snapshot-pids ' + (($targets | ForEach-Object {{ $_ }}) -join ','))
  $deadline = (Get-Date).AddSeconds({int(graceful_seconds)})
  while ((Get-Date) -lt $deadline) {{
    $alive = @($targets | Where-Object {{ Get-Process -Id $_ -ErrorAction SilentlyContinue }})
    if ($alive.Count -eq 0) {{ break }}
    Start-Sleep -Milliseconds 400
  }}
  $survivors = @($targets | Where-Object {{ Get-Process -Id $_ -ErrorAction SilentlyContinue }})
  if ($survivors.Count -gt 0) {{
    Write-UpdateLog ('force-stop ' + ($survivors -join ','))
    foreach ($targetId in $survivors) {{
      Stop-Process -Id $targetId -Force -ErrorAction SilentlyContinue
    }}
  }}
  # Always clear the image tree — WebView hosts often ignore Stop-Process.
  # Do not let a "not found" exit code abort the handoff under -ErrorAction Stop.
  cmd.exe /c ("taskkill /F /IM " + {image_literal} + " /T >nul 2>&1") | Out-Null
  $forceDeadline = (Get-Date).AddSeconds({int(force_wait_seconds)})
  while ((Get-Date) -lt $forceDeadline) {{
    $alive = @(Get-Process -Name {process_literal} -ErrorAction SilentlyContinue)
    if ($alive.Count -eq 0) {{ break }}
    cmd.exe /c ("taskkill /F /IM " + {image_literal} + " /T >nul 2>&1") | Out-Null
    Start-Sleep -Milliseconds 400
  }}
  $stillAlive = @(Get-Process -Name {process_literal} -ErrorAction SilentlyContinue)
  if ($stillAlive.Count -gt 0) {{
    throw ('Unable to stop process(es): ' + (($stillAlive | ForEach-Object {{ $_.Id }}) -join ','))
  }}
  if (Test-Path -LiteralPath $lockPath) {{
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    Write-UpdateLog 'lock-cleared'
  }}
  Write-UpdateLog 'install-start'
  $p = Start-Process -FilePath {installer_literal} -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -PassThru -Wait
  if ($null -eq $p) {{ throw 'Installer process failed to start.' }}
  if ($p.ExitCode -ne 0) {{
    throw ('Installer exited with code ' + $p.ExitCode)
  }}
  Write-UpdateLog 'install-ok'
  Write-UpdateLog 'relaunch-delegated-to-installer'
}} catch {{
  Write-UpdateLog ('handoff-error ' + $_.Exception.Message)
  exit 1
}}
"""


def _installer_command(
    installer: Path,
    installed_exe: Path,
    *,
    log_path: Path,
    lock_path: Path | None = None,
    process_name: str = PROCESS_NAME,
    graceful_seconds: int = GRACEFUL_EXIT_SECONDS,
    force_wait_seconds: int = FORCE_STOP_WAIT_SECONDS,
) -> list[str]:
    """Write a handoff script to disk and return a powershell -File invocation.

    Inline ``-Command`` scripts launched from the frozen WebView host were dying
    before ``handoff-start`` could be written. A on-disk ``.ps1`` plus ``-File``
    survives parent teardown much more reliably.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    script_path = log_path.parent / "update-handoff.ps1"
    script_path.write_text(
        _handoff_script_text(
            installer,
            installed_exe,
            log_path=log_path,
            lock_path=lock_path or _desktop_lock_path(),
            process_name=process_name,
            graceful_seconds=graceful_seconds,
            force_wait_seconds=force_wait_seconds,
        ),
        encoding="utf-8-sig",
    )
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ]


def _launch_installer(installer: Path) -> None:
    """Start the detached installer handoff. Caller exits the app process."""
    installed_exe = _installed_executable()
    log_path = _update_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = _installer_command(
        installer,
        installed_exe,
        log_path=log_path,
        lock_path=_desktop_lock_path(),
    )
    # ``cmd /c start`` breaks away from the parent console/job so the handoff
    # keeps running after the WebView host is force-killed.
    wrapped = ["cmd.exe", "/c", "start", "", "/MIN", *command]
    _write_update_progress(f"launch-handoff installer={installer}")
    _spawn_detached(wrapped)


def _release_desktop_lock() -> None:
    """Drop the single-instance lock before force-killing so relaunch can acquire it."""
    try:
        from src.desktop import release_instance_lock

        release_instance_lock()
        _write_update_progress("desktop-lock-released")
    except Exception as exc:  # noqa: BLE001 — best effort before hard exit
        _write_update_progress(f"desktop-lock-release-failed {type(exc).__name__}: {exc}")
    lock_path = _desktop_lock_path()
    try:
        lock_path.unlink(missing_ok=True)
        _write_update_progress("desktop-lock-unlinked")
    except OSError as exc:
        _write_update_progress(f"desktop-lock-unlink-failed {exc}")


def _force_exit_process_tree() -> None:
    """Exit even when ``os._exit`` is ignored by a frozen WebView host thread."""
    _release_desktop_lock()
    pid = os.getpid()
    _write_update_progress(f"self-exit pid={pid}")
    try:
        _spawn_detached(["taskkill.exe", "/F", "/PID", str(pid), "/T"])
    except OSError:
        pass
    try:
        _spawn_detached(["taskkill.exe", "/F", "/IM", f"{PROCESS_NAME}.exe", "/T"])
    except OSError:
        pass
    time.sleep(0.3)
    os._exit(0)


def _install_worker(release: dict[str, Any]) -> None:
    """Download, verify, hand off, then hard-exit so Setup can replace files."""
    try:
        _write_update_progress(f"worker-start target={release['latest_version']}")
        update_dir = USER_DATA_ROOT / "updates"
        update_dir.mkdir(parents=True, exist_ok=True)
        installer = update_dir / f"StatementPipelineSetup-{release['latest_version']}.exe"
        _write_update_progress("download-start")
        _download(release["installer_url"], installer)
        _write_update_progress(f"download-ok size={installer.stat().st_size}")
        _write_update_progress("checksum-start")
        if _file_checksum(installer) != _expected_checksum(release["checksum_url"]):
            installer.unlink(missing_ok=True)
            _write_update_progress("checksum-mismatch")
            return
        _write_update_progress("checksum-ok")
        _launch_installer(installer)
        _write_update_progress("exit-scheduled")
        # Give the breakaway handoff a moment to write handoff-start before we die.
        time.sleep(1.5)
        _force_exit_process_tree()
    except Exception as exc:  # noqa: BLE001 — log and keep the running app usable
        _write_update_progress(f"worker-error {type(exc).__name__}: {exc}")
        _show_update_error(
            "The update could not start the installer handoff.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Your current version is still running. Close Statement Pipeline, then run "
            "the downloaded Setup from:\n"
            f"{USER_DATA_ROOT / 'updates'}"
        )


def _start_install_worker(release: dict[str, Any]) -> None:
    """Spawn the install worker. Tests may monkeypatch this to run synchronously."""
    threading.Thread(target=_install_worker, args=(release,), daemon=True, name="statement-pipeline-update").start()


def install_latest_update() -> dict[str, str]:
    """Begin download/install on a background thread and return immediately.

    The HTTP request must not stay open for the multi-minute download, or the UI
    appears frozen and a failed Timer-based exit can leave the process hung with
    a completed installer on disk and no ``update.log`` handoff.
    """
    if not _windows_desktop_build():
        raise UpdateError("In-app updates are only available in the installed Windows application.")

    release = _latest_release()
    if _version_key(release["latest_version"]) <= _version_key(APP_VERSION):
        raise UpdateError("You are already using the latest version.")

    _write_update_progress(f"install-accepted current={APP_VERSION} latest={release['latest_version']}")
    _start_install_worker(release)
    return {"message": "Update started. Statement Pipeline will restart shortly."}
