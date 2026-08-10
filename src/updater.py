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


def _installer_command(installer: Path, installed_exe: Path) -> list[str]:
    """Build a tokenized ``cmd`` invocation for update + relaunch.

    ``start`` is a cmd built-in whose first quoted argument is its title.  Do
    not hand cmd one pre-quoted script string: Windows can then parse a path
    containing spaces as ``\\``.  Supplying each token separately lets
    ``subprocess`` apply Windows command-line quoting exactly once.
    """
    return [
        "cmd.exe",
        "/d",
        "/c",
        "start",
        "",
        "/wait",
        str(installer),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "&&",
        "start",
        "",
        str(installed_exe),
    ]


def _launch_installer(installer: Path) -> None:
    """Run the in-place installer after this server has had time to reply."""
    installed_exe = _installed_executable()
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        _installer_command(installer, installed_exe),
        close_fds=True,
        creationflags=flags,
    )
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
