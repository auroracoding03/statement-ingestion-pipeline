"""Context-tag vocabulary helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tags import create_tag, delete_tag, list_tags, normalize_tag_ids, slugify


def test_slugify_trip_label() -> None:
    assert slugify("London-Paris") == "london-paris"
    assert slugify("  Milan / Zermatt ") == "milan-zermatt"


def test_normalize_tag_ids_handles_empty_and_lists() -> None:
    assert normalize_tag_ids(None) == []
    assert normalize_tag_ids(["date", "date", "gift"]) == ["date", "gift"]
    assert normalize_tag_ids("date|gift") == ["date", "gift"]


def test_create_list_delete_tags(tmp_path: Path) -> None:
    path = tmp_path / "tags.yaml"
    path.write_text("tags: []\n", encoding="utf-8")

    created = create_tag(label="London-Paris", kind="trip", path=path)
    assert created == {"id": "london-paris", "label": "London-Paris", "kind": "trip"}
    assert list_tags(path)[0]["id"] == "london-paris"

    with pytest.raises(ValueError, match="already exists"):
        create_tag(label="London Paris", kind="trip", path=path)

    assert delete_tag("london-paris", path=path) is True
    assert list_tags(path) == []
