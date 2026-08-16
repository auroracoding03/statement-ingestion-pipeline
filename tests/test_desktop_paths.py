from __future__ import annotations

from pathlib import Path

from src.paths import APP_NAME, _runtime_roots, seed_default_config
from src.version import APP_DISPLAY_NAME


def test_display_name_and_data_folder_stay_distinct() -> None:
    assert APP_DISPLAY_NAME == "Family Finance"
    assert APP_NAME == "Statement Pipeline"


def test_frozen_windows_uses_local_app_data(tmp_path: Path) -> None:
    assets = tmp_path / "bundle"
    local_app_data = tmp_path / "LocalAppData"

    actual_assets, actual_data = _runtime_roots(
        frozen=True,
        platform="win32",
        local_app_data=str(local_app_data),
        bundle_root=assets,
    )

    assert actual_assets == assets
    assert actual_data == local_app_data / APP_NAME


def test_source_checkout_keeps_existing_layout(tmp_path: Path) -> None:
    assets, data = _runtime_roots(frozen=False, source_root=tmp_path)

    assert assets == tmp_path
    assert data == tmp_path


def test_statement_pipeline_home_overrides_user_data(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "household"
    monkeypatch.setenv("STATEMENT_PIPELINE_HOME", str(home))
    assets, data = _runtime_roots(frozen=False, source_root=tmp_path / "checkout")

    assert assets == tmp_path / "checkout"
    assert data == home


def test_seed_default_config_copies_only_missing_files(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "rules.yaml").write_text("rules: []\n")
    (defaults / "merchants.yaml").write_text("merchants: []\n")
    user_config = tmp_path / "user-config"
    user_config.mkdir()
    (user_config / "rules.yaml").write_text("rules: [kept]\n")

    seed_default_config(user_config, defaults)

    assert (user_config / "rules.yaml").read_text() == "rules: [kept]\n"
    assert (user_config / "merchants.yaml").read_text() == "merchants: []\n"
