[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ is required to create a release build."
}

$innoSetup = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $innoSetup)) {
    throw "Inno Setup 6 was not found at '$innoSetup'. Install Inno Setup, then rerun this script."
}

npm ci --prefix ui
npm run build --prefix ui
python -m pip install --upgrade pip
python -m pip install -e ".[package]"
python -m PyInstaller packaging\StatementPipeline.spec --clean --noconfirm

$version = python -c "from pathlib import Path; import tomllib; print(tomllib.loads(Path('pyproject.toml').read_text())['project']['version'])"
& $innoSetup "/DMyAppVersion=$version" packaging\windows-installer.iss
