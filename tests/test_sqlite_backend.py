#!/usr/bin/env python3
"""Tests for the SQLite state backend."""

import sqlite3
import threading

import pytest

from odsc.backends.sqlite_backend import SqliteStateBackend


def test_initializes_schema_and_wal_mode(tmp_path):
    """Backend startup should create tables and enable WAL mode."""
    backend = SqliteStateBackend(tmp_path / "state.db")

    tables = {
        row[0]
        for row in backend.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    journal_mode = backend.conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert {"file_cache", "sync_state", "metadata"} <= tables
    assert backend.get_metadata("schema_version") == "1"
    assert journal_mode.lower() == "wal"

    backend.close()


def test_creates_missing_parent_directory_and_database_file(tmp_path):
    """Initializing the backend should create the database path if needed."""
    db_path = tmp_path / "nested" / "state.db"

    backend = SqliteStateBackend(db_path)

    assert db_path.parent.is_dir()
    assert db_path.exists()
    backend.close()


def test_file_cache_crud_roundtrip(tmp_path):
    """Cache entries should round-trip through insert, lookup, and delete."""
    backend = SqliteStateBackend(tmp_path / "state.db")
    backend.set_file_cache(
        "docs/readme.txt",
        {
            "id": "file-1",
            "size": 123,
            "mtime_remote": 7.5,
            "eTag": "etag-1",
            "parentReference": {"id": "docs-folder"},
            "createdDateTime": "2024-01-01T00:00:00",
            "lastModifiedDateTime": "2024-01-02T00:00:00",
        },
    )
    backend.set_file_cache("docs", {"id": "folder-1", "is_folder": True, "parent_id": "root"})

    file_entry = backend.get_file_cache("docs/readme.txt")
    folder_entry = backend.get_file_cache("docs")

    assert file_entry == {
        "id": "file-1",
        "size": 123,
        "mtime_remote": 7.5,
        "eTag": "etag-1",
        "parentReference": {"id": "docs-folder"},
        "createdDateTime": "2024-01-01T00:00:00",
        "lastModifiedDateTime": "2024-01-02T00:00:00",
    }
    assert folder_entry["is_folder"] is True
    assert folder_entry["folder"] == {}

    backend.delete_file_cache("docs/readme.txt")
    assert backend.get_file_cache("docs/readme.txt") is None
    backend.close()


def test_sync_state_and_metadata_roundtrip(tmp_path):
    """Sync entries and metadata should preserve normalized values."""
    backend = SqliteStateBackend(tmp_path / "state.db")
    backend.set_sync_state(
        "docs/readme.txt",
        {
            "mtime": 10.5,
            "size": 456,
            "downloaded": True,
            "eTag": "sync-etag",
            "remote_modified": "2024-01-03T00:00:00",
            "upload_error": "transient failure",
        },
    )
    backend.set_metadata("delta_token", "cursor-1")

    assert backend.get_sync_state("docs/readme.txt") == {
        "mtime": 10.5,
        "size": 456,
        "downloaded": True,
        "eTag": "sync-etag",
        "remote_modified": "2024-01-03T00:00:00",
        "upload_error": "transient failure",
    }
    assert backend.get_metadata("delta_token") == "cursor-1"
    backend.close()


def test_save_load_replaces_existing_state(tmp_path):
    """A full save should replace old rows and be readable after reload."""
    db_path = tmp_path / "state.db"
    backend = SqliteStateBackend(db_path)
    backend.set_file_cache("stale.txt", {"id": "old", "size": 1})
    backend.set_sync_state("stale.txt", {"mtime": 1.0, "size": 1})

    backend.save(
        {
            "file_cache": {"fresh.txt": {"id": "new-id", "size": 9, "eTag": "etag-new"}},
            "files": {"fresh.txt": {"mtime": 2.0, "size": 9, "downloaded": True, "eTag": "etag-new"}},
            "delta_token": "cursor-2",
            "last_sync": "2024-01-04T00:00:00",
        }
    )
    backend.close()

    reloaded = SqliteStateBackend(db_path).load()

    assert "stale.txt" not in reloaded["file_cache"]
    assert "stale.txt" not in reloaded["files"]
    assert reloaded["file_cache"]["fresh.txt"]["id"] == "new-id"
    assert reloaded["files"]["fresh.txt"]["downloaded"] is True
    assert reloaded["delta_token"] == "cursor-2"
    assert reloaded["last_sync"] == "2024-01-04T00:00:00"


def test_concurrent_writes_share_single_connection_safely(tmp_path):
    """The write lock should serialize concurrent updates without data loss."""
    backend = SqliteStateBackend(tmp_path / "state.db")
    errors = []
    start = threading.Barrier(6)

    def worker(index):
        try:
            start.wait()
            backend.set_file_cache(
                f"file-{index}.txt",
                {"id": f"id-{index}", "size": index, "eTag": f"etag-{index}"},
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()

    cache = backend.get_all_file_cache()
    assert errors == []
    assert len(cache) == 5
    assert cache["file-4.txt"]["eTag"] == "etag-4"
    backend.close()


def test_corrupt_database_raises_database_error(tmp_path):
    """Invalid database contents should surface as SQLite errors."""
    db_path = tmp_path / "corrupt.db"
    db_path.write_text("not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        SqliteStateBackend(db_path)
