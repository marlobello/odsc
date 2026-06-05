#!/usr/bin/env python3
"""Characterization tests for data-safety behavior in sync.

These pin the fixes for:
- remote-deletion trash failure must not drop sync state (would re-upload),
- moving a tracked directory out of the sync root must not corrupt state,
- get_delta must never hand back a None continuation token,
- large files must use a resumable upload session.
"""

import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from odsc import daemon as daemon_module
from odsc.onedrive_client import OneDriveClient
from odsc.sync_state import SyncStateManager


class DummyConfig:
    """Minimal config stub for daemon safety tests."""

    def __init__(self, tmp_path: Path):
        self.config_dir = tmp_path
        self.sync_directory = tmp_path / "sync"
        self.force_sync_path = tmp_path / ".force_sync"
        self.token_path = tmp_path / ".token"
        self.log_path = tmp_path / "odsc.log"
        self.log_level = "INFO"
        self.client_id = ""
        self.sync_interval = 0
        self.download_chunk_size = 4096
        self.saved_states = []

    def load_state(self):
        return {}

    def save_state(self, state):
        self.saved_states.append(state)

    def load_token(self):
        return {"access_token": "token"}

    def close(self):
        pass


@pytest.fixture(autouse=True)
def patch_signal_registration(monkeypatch):
    monkeypatch.setattr(daemon_module.signal, "signal", lambda *a, **kw: None)


@pytest.fixture
def daemon(tmp_path):
    config = DummyConfig(tmp_path)
    config.sync_directory.mkdir(parents=True, exist_ok=True)
    return daemon_module.SyncDaemon(config)


# --------------------------------------------------------------------------- #
# Remote-deletion trash failure must preserve state (no re-upload)            #
# --------------------------------------------------------------------------- #

def test_move_to_recycle_bin_returns_true_on_success(monkeypatch, daemon):
    monkeypatch.setattr(daemon_module, "send2trash", lambda p: None)
    local = daemon.config.sync_directory / "file.txt"
    local.write_text("data")
    assert daemon._move_to_recycle_bin(local, "file.txt") is True


def test_move_to_recycle_bin_returns_false_when_trash_fails(monkeypatch, daemon):
    def boom(_p):
        raise OSError("no trash service")

    monkeypatch.setattr(daemon_module, "send2trash", boom)
    local = daemon.config.sync_directory / "file.txt"
    local.write_text("data")
    assert daemon._move_to_recycle_bin(local, "file.txt") is False
    # File must be left in place — never permanently deleted.
    assert local.exists()


def test_recycle_remote_deleted_keeps_state_when_trash_fails(monkeypatch, daemon):
    """A failed trash move must keep sync state so the file is not re-uploaded,
    and must drop the cache entry so the next sync retries the deletion."""
    rel = "file.txt"
    local = daemon.config.sync_directory / rel
    local.write_text("data")
    daemon.state_mgr.set_file_entry(rel, mtime=1.0, size=4, metadata={"eTag": "e"})
    daemon.state_mgr.set_cache_entry(rel, {"id": "remote-id", "eTag": "e"})

    monkeypatch.setattr(daemon_module, "send2trash", Mock(side_effect=OSError("boom")))
    daemon._recycle_remote_deleted_file(rel, daemon.config.sync_directory)

    # State retained → not re-uploaded; cache dropped → next sync sees local-only.
    state_entry = daemon.state_mgr.get_file_entry(rel)
    assert state_entry != {}
    assert daemon.state_mgr.get_cache_entry(rel) is None
    assert daemon.state_mgr.get_deletion_failure_count(rel) == 1

    # The next sync must reclassify it as a deletion to retry (not an upload).
    action = daemon.decision_engine.determine_action(rel, {"mtime": 1.0, "size": 4}, None, state_entry)
    assert action == "recycle"


def test_recycle_remote_deleted_removes_state_on_success(monkeypatch, daemon):
    rel = "file.txt"
    local = daemon.config.sync_directory / rel
    local.write_text("data")
    daemon.state_mgr.set_file_entry(rel, mtime=1.0, size=4, metadata={"eTag": "e"})

    monkeypatch.setattr(daemon_module, "send2trash", lambda p: None)
    daemon._recycle_remote_deleted_file(rel, daemon.config.sync_directory)

    assert daemon.state_mgr.get_file_entry(rel) == {}


# --------------------------------------------------------------------------- #
# Moving a tracked directory out of the sync root must not corrupt state       #
# --------------------------------------------------------------------------- #

def test_remove_entries_with_prefix_clears_subtree():
    mgr = SyncStateManager(lambda: {}, lambda state: None)
    mgr.set_file_entry("Photos/a.jpg", 1.0, 10, {"eTag": "1"})
    mgr.set_file_entry("Photos/sub/b.jpg", 1.0, 10, {"eTag": "2"})
    mgr.set_file_entry("Docs/keep.txt", 1.0, 10, {"eTag": "3"})
    mgr.set_cache_entry("Photos/a.jpg", {"id": "x"})

    removed = mgr.remove_entries_with_prefix("Photos")

    assert removed >= 2
    remaining = mgr.all_tracked_paths()
    assert remaining == ["Docs/keep.txt"]
    assert mgr.get_cache_entry("Photos/a.jpg") is None


def test_sync_move_directory_out_of_root_does_not_corrupt_paths(daemon):
    """Out-of-root directory move stops tracking the subtree without rewriting keys."""
    sync_dir = daemon.config.sync_directory
    daemon.state_mgr.set_file_entry("Photos/a.jpg", 1.0, 10, {"eTag": "1"})
    daemon.state_mgr.set_file_entry("Photos/b.jpg", 1.0, 10, {"eTag": "2"})

    src = sync_dir / "Photos"
    dst = sync_dir.parent / "elsewhere" / "Photos"  # outside the sync root

    daemon._sync_move(src, dst, is_dir=True)

    paths = daemon.state_mgr.all_tracked_paths()
    # No leftover subtree entries and, crucially, no corrupted leading-slash keys.
    assert "Photos/a.jpg" not in paths
    assert "Photos/b.jpg" not in paths
    assert not any(p.startswith("/") for p in paths)


# --------------------------------------------------------------------------- #
# get_delta must never return a None continuation token                        #
# --------------------------------------------------------------------------- #

class _DeltaResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_get_delta_returns_changes_and_token(monkeypatch):
    client = OneDriveClient(token_data={"access_token": "t", "expires_at": 10**12})
    monkeypatch.setattr(
        client,
        "_api_request_url",
        lambda url, **kw: _DeltaResponse(
            {"value": [{"id": "1"}], "@odata.deltaLink": "https://graph/next"}
        ),
    )

    changes, token = client.get_delta(None)

    assert changes == [{"id": "1"}]
    assert token == "https://graph/next"


def test_get_delta_raises_without_delta_link(monkeypatch):
    client = OneDriveClient(token_data={"access_token": "t", "expires_at": 10**12})
    monkeypatch.setattr(
        client,
        "_api_request_url",
        lambda url, **kw: _DeltaResponse({"value": []}),
    )

    with pytest.raises(RuntimeError):
        client.get_delta(None)


# --------------------------------------------------------------------------- #
# Large files must use a resumable upload session                              #
# --------------------------------------------------------------------------- #

class _UploadResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_small_file_uses_simple_put(tmp_path, monkeypatch):
    client = OneDriveClient(token_data={"access_token": "t", "expires_at": 10**12})
    f = tmp_path / "small.txt"
    f.write_bytes(b"hello")

    used = {"simple": False, "session": False}
    monkeypatch.setattr(
        client, "_api_request",
        lambda *a, **kw: used.__setitem__("simple", True) or _UploadResponse(200, {"eTag": "e"}),
    )
    monkeypatch.setattr(
        client, "_upload_large_file",
        lambda *a, **kw: used.__setitem__("session", True) or {},
    )

    client.upload_file(f, "small.txt")

    assert used["simple"] is True
    assert used["session"] is False


def test_large_file_uses_upload_session_in_fragments(tmp_path, monkeypatch):
    client = OneDriveClient(token_data={"access_token": "t", "expires_at": 10**12})
    client.SIMPLE_UPLOAD_MAX_BYTES = 5
    client.UPLOAD_FRAGMENT_SIZE = 4

    f = tmp_path / "big.bin"
    f.write_bytes(b"abcdefghij")  # 10 bytes -> fragments of 4,4,2

    monkeypatch.setattr(client, "_create_upload_session", lambda rp: "https://upload.example/url")

    sent = []

    def fake_put(url, data=None, headers=None, timeout=None):
        sent.append((headers["Content-Range"], len(data)))
        # Determine whether this is the final fragment from the Content-Range.
        rng = headers["Content-Range"].split(" ", 1)[1]
        span, total = rng.split("/")
        end = int(span.split("-")[1])
        if end + 1 == int(total):
            return _UploadResponse(201, {"id": "remote", "eTag": "final"})
        return _UploadResponse(202, {})

    monkeypatch.setattr(client._session, "put", fake_put)

    metadata = client.upload_file(f, "big.bin")

    assert metadata["eTag"] == "final"
    assert [n for _, n in sent] == [4, 4, 2]
    assert sent[0][0] == "bytes 0-3/10"
    assert sent[-1][0] == "bytes 8-9/10"


def test_upload_session_error_redacts_upload_url(tmp_path, monkeypatch):
    """A failed fragment upload must not leak the pre-authenticated upload URL."""
    import requests

    client = OneDriveClient(token_data={"access_token": "t", "expires_at": 10**12})
    client.SIMPLE_UPLOAD_MAX_BYTES = 1
    client.UPLOAD_FRAGMENT_SIZE = 4

    f = tmp_path / "big.bin"
    f.write_bytes(b"abcdef")

    secret_url = "https://upload.example/session?token=SECRET123"
    monkeypatch.setattr(client, "_create_upload_session", lambda rp: secret_url)
    monkeypatch.setattr(client._session, "delete", lambda *a, **k: None)

    def failing_put(url, data=None, headers=None, timeout=None):
        raise requests.exceptions.ConnectionError(f"failed connecting to {url}")

    monkeypatch.setattr(client._session, "put", failing_put)

    with pytest.raises(requests.exceptions.ConnectionError) as exc_info:
        client.upload_file(f, "big.bin")

    assert "SECRET123" not in str(exc_info.value)
    assert "<redacted-upload-url>" in str(exc_info.value)
