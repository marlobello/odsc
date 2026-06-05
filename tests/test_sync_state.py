#!/usr/bin/env python3
"""Tests for SyncStateManager copy isolation."""

from odsc.sync_state import SyncStateManager


def test_save_deep_copies_nested_state():
    """Saved snapshots should not share nested dicts with live state."""
    saved_snapshots = []
    manager = SyncStateManager(lambda: {}, saved_snapshots.append)

    manager.set_cache_entry("folder/file.txt", {"nested": {"version": 1}})
    manager.save()

    manager._state["file_cache"]["folder/file.txt"]["nested"]["version"] = 2

    assert saved_snapshots[0]["file_cache"]["folder/file.txt"]["nested"]["version"] == 1


def test_load_deep_copies_backend_state():
    """Loaded state should not share nested dicts with the backend payload."""
    backend_state = {
        "files": {"folder/file.txt": {"nested": {"version": 1}}},
        "file_cache": {"folder/file.txt": {"nested": {"version": 1}}},
    }
    manager = SyncStateManager(lambda: backend_state, lambda state: None)

    manager.load()
    backend_state["file_cache"]["folder/file.txt"]["nested"]["version"] = 2
    backend_state["files"]["folder/file.txt"]["nested"]["version"] = 2

    assert manager.get_cache_entry("folder/file.txt")["nested"]["version"] == 1
    assert manager.get_file_entry("folder/file.txt")["nested"]["version"] == 1


def test_public_read_methods_return_deep_copies():
    """Mutable values returned by read APIs should be isolated copies."""
    manager = SyncStateManager(lambda: {}, lambda state: None)

    manager.patch_file_entries({"folder/file.txt": {"nested": {"version": 1}}})
    manager.set_cache_entry("folder/file.txt", {"nested": {"version": 1}})

    file_entry = manager.get_file_entry("folder/file.txt")
    cache_entry = manager.get_cache_entry("folder/file.txt")
    cache_items = manager.all_cache_items()

    file_entry["nested"]["version"] = 2
    cache_entry["nested"]["version"] = 2
    cache_items[0][1]["nested"]["version"] = 2

    assert manager.get_file_entry("folder/file.txt")["nested"]["version"] == 1
    assert manager.get_cache_entry("folder/file.txt")["nested"]["version"] == 1


def test_persist_file_uses_incremental_hook():
    """persist_file should write a single entry, not rewrite the whole DB."""
    saves = []
    entries = []
    mgr = SyncStateManager(
        lambda: {}, lambda s: saves.append(s), lambda p, e: entries.append((p, e))
    )
    mgr.set_file_entry("a.txt", 1.0, 5, {"eTag": "e", "quickXorHash": "H"})

    mgr.persist_file("a.txt")

    assert len(entries) == 1
    assert entries[0][0] == "a.txt"
    assert entries[0][1]["quickXorHash"] == "H"
    assert saves == []  # no full-state rewrite


def test_persist_file_falls_back_to_full_save_without_hook():
    """With no incremental hook, persist_file must still persist via save()."""
    saves = []
    mgr = SyncStateManager(lambda: {}, lambda s: saves.append(s))
    mgr.set_file_entry("a.txt", 1.0, 5, {"eTag": "e"})

    mgr.persist_file("a.txt")

    assert len(saves) == 1


def test_persist_file_missing_entry_falls_back_to_save():
    """A removed entry must be flushed via a full save (so the removal lands)."""
    saves = []
    entries = []
    mgr = SyncStateManager(
        lambda: {}, lambda s: saves.append(s), lambda p, e: entries.append((p, e))
    )

    mgr.persist_file("ghost.txt")

    assert entries == []
    assert len(saves) == 1
