"""Safe in-place updates for the installed Windows application."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
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
GRACEFUL_EXIT_SECONDS = 8
FORCE_STOP_WAIT_SECONDS = 15


class UpdateError(RuntimeError):
    """A user-safe update failure that can be shown by the local API."""


def _windows_desktop_build() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _version_key(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise UpdateError(f"Unsupported release version: {value!r}")
    return tuple(int(part) for part in match.groups())


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "StatementPipeline"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
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
        with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise UpdateError("The update download failed. Your installed version was not changed.") from exc


def _expected_checksum(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "StatementPipeline"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
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


def _update_log_path() -> Path:
    return USER_DATA_ROOT / "updates" / "update.log"


def _installer_command(
    installer: Path,
    installed_exe: Path,
    *,
    log_path: Path,
    process_name: str = PROCESS_NAME,
    graceful_seconds: int = GRACEFUL_EXIT_SECONDS,
    force_wait_seconds: int = FORCE_STOP_WAIT_SECONDS,
) -> list[str]:
    """Snapshot live app PIDs, stop survivors, then install and relaunch once.

    The previous waiter only watched the updating PID. If that process failed to
    exit (or a failed duplicate already existed), the replacement exe hit the
    single-instance lock and reported "already running." Snapshotting every
    pre-install app PID keeps the wait bounded and avoids later matching the
    newly launched version by process name.
    """
    installer_literal = json.dumps(str(installer))
    exe_literal = json.dumps(str(installed_exe))
    log_literal = json.dumps(str(log_path))
    process_literal = json.dumps(process_name)
    script = f"""
$ErrorActionPreference = 'Stop'
$logPath = {log_literal}
function Write-UpdateLog([string]$Message) {{
  $line = '{{0:u}} {{1}}' -f (Get-Date).ToUniversalTime(), $Message
  Add-Content -LiteralPath $logPath -Value $line
}}
try {{
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null
  Write-UpdateLog 'handoff-start'
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
    $forceDeadline = (Get-Date).AddSeconds({int(force_wait_seconds)})
    while ((Get-Date) -lt $forceDeadline) {{
      $alive = @($survivors | Where-Object {{ Get-Process -Id $_ -ErrorAction SilentlyContinue }})
      if ($alive.Count -eq 0) {{ break }}
      Start-Sleep -Milliseconds 400
    }}
    $stillAlive = @($survivors | Where-Object {{ Get-Process -Id $_ -ErrorAction SilentlyContinue }})
    if ($stillAlive.Count -gt 0) {{
      throw ('Unable to stop process(es): ' + ($stillAlive -join ','))
    }}
  }}
  Write-UpdateLog 'install-start'
  $p = Start-Process -FilePath {installer_literal} -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -PassThru -Wait
  if ($null -eq $p) {{ throw 'Installer process failed to start.' }}
  if ($p.ExitCode -ne 0) {{
    throw ('Installer exited with code ' + $p.ExitCode)
  }}
  Write-UpdateLog 'install-ok'
  Start-Process -FilePath {exe_literal}
  Write-UpdateLog 'relaunch-ok'
}} catch {{
  Write-UpdateLog ('handoff-error ' + $_.Exception.Message)
  exit 1
}}
"""
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]


def _launch_installer(installer: Path) -> None:
    """Run the in-place installer after this process has exited."""
    installed_exe = _installed_executable()
    log_path = _update_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        _installer_command(installer, installed_exe, log_path=log_path),
        close_fds=True,
        creationflags=flags,
    )
    # Hard-exit so the waiter observes this PID disappearing promptly. The
    # detached PowerShell script remains authoritative if graceful exit fails.
    threading.Timer(0.75, lambda: os._exit(0)).start()


def install_latest_update() -> dict[str, str]:
    """Download, checksum-verify, and silently install a newer release."""
    if not _windows_desktop_build():
        raise UpdateError("In-app updates are only available in the installed Windows application.")

    release = _latest_release()
    if _version_key(release["latest_version"]) <= _version_key(APP_VERSION):
        raise UpdateError("You are already using the latest version.")

    update_dir = USER_DATA_ROOT / "updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    installer = update_dir / f"StatementPipelineSetup-{release['latest_version']}.exe"
    _download(release["installer_url"], installer)
    if _file_checksum(installer) != _expected_checksum(release["checksum_url"]):
        installer.unlink(missing_ok=True)
        raise UpdateError("The update checksum did not match. Your installed version was not changed.")

    _launch_installer(installer)
    return {"message": "Update downloaded. Statement Pipeline will restart shortly."}
