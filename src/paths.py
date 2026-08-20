"""Runtime paths for both developer and installed desktop builds.

Source checkouts keep state alongside the project for a convenient developer
workflow.  The frozen Windows application instead reads bundled assets from the
installation and writes all personal data below ``%LOCALAPPDATA%``.  This keeps
upgrades and uninstalls from touching a user's statements or ledger.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


# Keep this folder name stable so installed ledgers stay under
# ``%LOCALAPPDATA%\Statement Pipeline``. The window title and Start menu name
# live on ``APP_DISPLAY_NAME`` in ``src.version``.
APP_NAME = "Statement Pipeline"
CONFIG_FILES = (
    "rules.yaml",
    "merchants.yaml",
    "expected_recurring.yaml",
    "publish.yaml",
    "ollama.yaml",
    "tags.yaml",
    "card_products.yaml",
    "budget.yaml",
)


def _runtime_roots(
    *,
    frozen: bool | None = None,
    platform: str | None = None,
    local_app_data: str | None = None,
    source_root: Path | None = None,
    bundle_root: Path | None = None,
) -> tuple[Path, Path]:
    """Return ``(asset_root, user_data_root)`` for the active runtime.

    ``bundle_root`` is supplied by PyInstaller through ``sys._MEIPASS``.  The
    optional arguments keep this policy easy to test without faking a frozen
    process.
    """
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    current_platform = sys.platform if platform is None else platform
    checkout = source_root or Path(__file__).resolve().parents[1]
    env_home = os.environ.get("STATEMENT_PIPELINE_HOME")
    if env_home:
        home = Path(env_home).expanduser()
        if is_frozen:
            assets = bundle_root or Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            return assets, home
        return checkout, home

    if not is_frozen:
        return checkout, checkout

    assets = bundle_root or Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    if current_platform == "win32":
        app_data = local_app_data or os.environ.get("LOCALAPPDATA")
        if app_data:
            return assets, Path(app_data) / APP_NAME
        return assets, Path.home() / "AppData" / "Local" / APP_NAME

    # A frozen build on another platform still avoids writing into the bundle.
    return assets, Path.home() / f".{APP_NAME.lower().replace(' ', '-')}"


ASSET_ROOT, USER_DATA_ROOT = _runtime_roots()

# ``ROOT`` remains the source/project root for compatibility with developer
# commands that generate a static publishable dashboard.
ROOT = ASSET_ROOT
INBOX = USER_DATA_ROOT / "inbox"
DATA = USER_DATA_ROOT / "data"
CONFIG = USER_DATA_ROOT / "config"
DASHBOARD = ASSET_ROOT / "dashboard"
UI = ASSET_ROOT / "ui"
DEFAULT_CONFIG = ASSET_ROOT / "config"

RULES_PATH = CONFIG / "rules.yaml"
MERCHANTS_PATH = CONFIG / "merchants.yaml"
EXPECTED_RECURRING_PATH = CONFIG / "expected_recurring.yaml"
PUBLISH_PATH = CONFIG / "publish.yaml"
OLLAMA_PATH = CONFIG / "ollama.yaml"
TAGS_PATH = CONFIG / "tags.yaml"
CARD_PRODUCTS_PATH = CONFIG / "card_products.yaml"
BUDGET_PATH = CONFIG / "budget.yaml"

LEDGER_PARQUET = DATA / "ledger.parquet"
LEDGER_LOCK = DATA / "ledger.lock"
FINANCE_DB = DATA / "finance.duckdb"
PROPOSALS_PARQUET = DATA / "proposals.parquet"
AI_PROPOSALS_PARQUET = DATA / "ai_proposals.parquet"
AI_APPLICATIONS_PATH = DATA / "ai_applications.json"
AI_SNAPSHOTS = DATA / "ai_snapshots"
INGEST_MANIFEST = DATA / "ingestion_manifest.parquet"
TRANSACTION_SOURCES_PARQUET = DATA / "transaction_sources.parquet"
SUPPRESSED_TXN_PATH = DATA / "suppressed_txn_ids.parquet"
RECURRING_PARQUET = DATA / "recurring.parquet"
RECONCILE_PARQUET = DATA / "reconciliation.parquet"
EXPORT_DIR = DATA / "export"
PENDING_UPLOADS = DATA / "pending_uploads"
LOGS_DIR = USER_DATA_ROOT / "logs"


def seed_default_config(
    config_dir: Path = CONFIG, default_config_dir: Path = DEFAULT_CONFIG
) -> None:
    """Seed missing user configuration without replacing existing choices."""
    config_dir.mkdir(parents=True, exist_ok=True)
    for filename in CONFIG_FILES:
        source = default_config_dir / filename
        destination = config_dir / filename
        if source.exists() and source != destination and not destination.exists():
            destination.write_bytes(source.read_bytes())


def ensure_dirs() -> None:
    """Create writable user directories and initialise default configuration."""
    INBOX.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_UPLOADS.mkdir(parents=True, exist_ok=True)
    AI_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    seed_default_config()
