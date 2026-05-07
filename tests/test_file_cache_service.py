#!/usr/bin/env python3
"""Tests for file cache service helpers."""

from odsc.services.file_cache_service import FileCacheService


def test_build_initial_cache_skips_deleted_items_and_sanitizes_paths():
    """Initial cache creation should ignore deleted items and normalize paths."""
    cache = FileCacheService.build_initial_cache(
        [
            {"id": "1", "name": "notes.txt", "parentReference": {"path": "/drive/root:/Docs"}},
            {"id": "2", "name": "removed.txt", "deleted": {"state": "deleted"}},
        ]
    )

    assert cache == {
        "Docs/notes.txt": {
            "id": "1",
            "name": "notes.txt",
            "parentReference": {"path": "/drive/root:/Docs"},
        }
    }


def test_process_delta_changes_updates_and_removes_entries():
    """Delta processing should merge new items and remove deleted ones by id."""
    updated = FileCacheService.process_delta_changes(
        [
            {"id": "1", "deleted": {"state": "deleted"}},
            {"id": "2", "name": "new.txt", "parentReference": {"path": "/drive/root:/Docs"}},
        ],
        {
            "Docs/old.txt": {"id": "1", "name": "old.txt"},
            "Docs/keep.txt": {"id": "keep", "name": "keep.txt"},
        },
    )

    assert "Docs/old.txt" not in updated
    assert updated["Docs/new.txt"]["id"] == "2"
    assert updated["Docs/keep.txt"]["id"] == "keep"


def test_process_delta_changes_ignores_invalid_items(caplog):
    """Malformed OneDrive paths should be skipped instead of breaking the update."""
    existing = {"safe.txt": {"id": "safe"}}

    updated = FileCacheService.process_delta_changes(
        [{"id": "2", "name": "bad.txt", "parentReference": {"path": "/drive/root:/../escape"}}],
        existing,
    )

    assert updated == existing
    assert "Error processing change" in caplog.text


def test_cache_to_file_list_adds_missing_name_and_cache_path():
    """List conversion should backfill missing names without mutating the source cache."""
    cache = {"Docs/report.txt": {"id": "1"}, "named.txt": {"id": "2", "name": "named.txt"}}

    file_list = FileCacheService.cache_to_file_list(cache)

    generated = next(item for item in file_list if item["id"] == "1")
    existing = next(item for item in file_list if item["id"] == "2")
    assert generated["name"] == "report.txt"
    assert generated["_cache_path"] == "Docs/report.txt"
    assert existing["name"] == "named.txt"
    assert "name" not in cache["Docs/report.txt"]
