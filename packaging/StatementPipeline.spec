# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller definition for the Windows desktop launcher.

Run this file on Windows only: PyInstaller produces native executables and does
not cross-compile macOS/Linux builds into Windows executables.
"""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules


PROJECT = Path(SPECPATH).parent
WINDOWS_ICON = PROJECT / "packaging" / "assets" / "statement-pipeline.ico"
datas = [
    (str(PROJECT / "ui" / "dist"), "ui/dist"),
    (str(PROJECT / "dashboard" / "public"), "dashboard/public"),
    (str(WINDOWS_ICON), "packaging/assets"),
]

# Configuration is seeded into the user's app-data directory on first launch.
for config_file in (PROJECT / "config").glob("*.yaml"):
    datas.append((str(config_file), "config"))

hiddenimports = collect_submodules("config")
if sys.platform == "win32":
    hiddenimports += collect_submodules("webview")

a = Analysis(
    [str(PROJECT / "src" / "desktop.py")],
    pathex=[str(PROJECT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    name="StatementPipeline",
    icon=str(WINDOWS_ICON) if sys.platform == "win32" else None,
    exclude_binaries=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="StatementPipeline",
)
