#!/usr/bin/env python3
"""Decision-matrix tests for SyncDecisionEngine.

These document and lock the local-vs-remote sync decision behavior that was
extracted from the daemon, including the deletion-classification heuristics.
"""

import pytest

from odsc.sync import SyncDecisionEngine


def make_engine(cache=None):
    cache = cache or {}
    return SyncDecisionEngine(lambda path: cache.get(path))


LOCAL = {"mtime": 1.0, "size": 10}
REMOTE = {"eTag": "e1", "lastModifiedDateTime": "2024-01-01T00:00:00", "size": 10}


def test_local_only_new_file_uploads():
    engine = make_engine()
    assert engine.determine_action("a.txt", LOCAL, None, {}) == "upload"


def test_local_only_in_cache_recycles():
    """A local-only file previously known on OneDrive was deleted remotely."""
    engine = make_engine(cache={"a.txt": {"id": "x"}})
    assert engine.determine_action("a.txt", LOCAL, None, {}) == "recycle"


def test_local_only_previously_synced_recycles():
    engine = make_engine()
    state = {"eTag": "old", "downloaded": True}
    assert engine.determine_action("a.txt", LOCAL, None, state) == "recycle"


def test_remote_only_new_file_skips():
    engine = make_engine()
    assert engine.determine_action("a.txt", None, REMOTE, {}) == "skip"


def test_remote_only_deleted_locally_skips():
    """A previously downloaded file now missing locally stays deleted."""
    engine = make_engine()
    state = {"downloaded": True, "eTag": "e1"}
    assert engine.determine_action("a.txt", None, REMOTE, state) == "skip"


def test_deleted_from_remote_this_cycle_recycles():
    engine = make_engine()
    assert engine.determine_action(
        "a.txt", LOCAL, REMOTE, {"downloaded": True}, deleted_from_remote={"a.txt"}
    ) == "recycle"


def test_both_unchanged_skips():
    engine = make_engine()
    state = {"mtime": 1.0, "size": 10, "downloaded": True, "eTag": "e1",
             "remote_modified": "2024-01-01T00:00:00"}
    assert engine.determine_action("a.txt", LOCAL, REMOTE, state) == "skip"


def test_local_changed_uploads():
    engine = make_engine()
    state = {"mtime": 0.0, "size": 5, "downloaded": True, "eTag": "e1",
             "remote_modified": "2024-01-01T00:00:00"}
    assert engine.determine_action("a.txt", LOCAL, REMOTE, state) == "upload"


def test_remote_changed_downloads():
    engine = make_engine()
    state = {"mtime": 1.0, "size": 10, "downloaded": True, "eTag": "old",
             "remote_modified": "2023-01-01T00:00:00"}
    assert engine.determine_action("a.txt", LOCAL, REMOTE, state) == "download"


def test_both_changed_conflict():
    engine = make_engine()
    state = {"mtime": 0.0, "size": 5, "downloaded": True, "eTag": "old",
             "remote_modified": "2023-01-01T00:00:00"}
    assert engine.determine_action("a.txt", LOCAL, REMOTE, state) == "conflict"


def test_both_present_untracked_same_size_skips():
    engine = make_engine()
    assert engine.determine_action("a.txt", LOCAL, REMOTE, {}) == "skip"


def test_both_present_untracked_different_size_conflict():
    engine = make_engine()
    remote = dict(REMOTE, size=999)
    assert engine.determine_action("a.txt", LOCAL, remote, {}) == "conflict"


def test_not_downloaded_skips():
    engine = make_engine()
    state = {"mtime": 0.0, "size": 5, "downloaded": False}
    assert engine.determine_action("a.txt", LOCAL, REMOTE, state) == "skip"
