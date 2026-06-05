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

    def persist_sync_entry(self, rel_path, entry):
        self.saved_states.append({"files": {rel_path: entry}})

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


# --------------------------------------------------------------------------- #
# Content-hash upload guard (echo suppression / no-op touch)                   #
# --------------------------------------------------------------------------- #

def test_upload_skipped_when_content_hash_matches(daemon):
    """A file whose content matches the last synced hash must not be re-uploaded
    (prevents the download->watchdog->upload echo and no-op touch uploads)."""
    from odsc.quickxorhash import quickxorhash_file

    rel = "file.txt"
    local = daemon.config.sync_directory / rel
    local.write_bytes(b"stable content")
    h = quickxorhash_file(local)
    # Simulate a prior sync that recorded this exact content hash.
    daemon.state_mgr.set_file_entry(rel, mtime=1.0, size=14, metadata={"eTag": "e", "quickXorHash": h})

    uploaded = []
    daemon.client = types.SimpleNamespace(upload_file=lambda *a, **k: uploaded.append(a) or {})

    # A later touch changes mtime/size record but not content.
    daemon._upload_file(rel, {"path": local, "mtime": 999.0, "size": 14})

    assert uploaded == []  # upload suppressed
    # mtime refreshed so future cycles short-circuit cheaply.
    assert daemon.state_mgr.get_file_entry(rel)["mtime"] == 999.0


def test_upload_proceeds_when_content_changed(daemon):
    """A real content change (hash differs) must still upload."""
    from odsc.quickxorhash import quickxorhash_file

    rel = "file.txt"
    local = daemon.config.sync_directory / rel
    local.write_bytes(b"original")
    old_hash = quickxorhash_file(local)
    daemon.state_mgr.set_file_entry(rel, mtime=1.0, size=8, metadata={"eTag": "e", "quickXorHash": old_hash})

    # Now the file content actually changes.
    local.write_bytes(b"edited content!!")

    uploaded = []
    daemon.client = types.SimpleNamespace(
        upload_file=lambda *a, **k: uploaded.append(a) or {"eTag": "e2"}
    )

    daemon._upload_file(rel, {"path": local, "mtime": 2.0, "size": 16})

    assert len(uploaded) == 1  # real change uploaded


def test_upload_proceeds_when_no_hash_recorded(daemon):
    """Without a recorded hash (legacy entries), uploads must proceed unchanged."""
    rel = "file.txt"
    local = daemon.config.sync_directory / rel
    local.write_bytes(b"data")
    daemon.state_mgr.set_file_entry(rel, mtime=1.0, size=4, metadata={"eTag": "e"})  # no hash

    uploaded = []
    daemon.client = types.SimpleNamespace(
        upload_file=lambda *a, **k: uploaded.append(a) or {"eTag": "e2"}
    )

    daemon._upload_file(rel, {"path": local, "mtime": 2.0, "size": 4})

    assert len(uploaded) == 1


# --------------------------------------------------------------------------- #
# Deletion tombstones (durable resurrection prevention)                        #
# --------------------------------------------------------------------------- #

def test_remote_deletion_writes_tombstone_with_hash(monkeypatch, daemon):
    """A remote deletion records a durable tombstone (with the deleted hash)."""
    rel = "file.txt"
    local = daemon.config.sync_directory / rel
    local.write_bytes(b"deleted content")
    daemon.state_mgr.set_cache_entry(rel, {"id": "rid", "eTag": "e", "quickXorHash": "HASH_DEL"})

    # Trash fails so the tombstone is retained (durable retry signal).
    monkeypatch.setattr(daemon_module, "send2trash", Mock(side_effect=OSError("boom")))
    daemon._process_remote_deletion({"id": "rid"})

    tomb = daemon.state_mgr.get_tombstone(rel)
    assert tomb is not None
    assert tomb["origin"] == "remote"
    assert tomb["quickXorHash"] == "HASH_DEL"


def test_remote_deletion_retires_tombstone_on_trash_success(monkeypatch, daemon):
    rel = "file.txt"
    daemon.state_mgr.set_cache_entry(rel, {"id": "rid", "eTag": "e", "quickXorHash": "H"})
    # No local file -> trash trivially succeeds.
    daemon._process_remote_deletion({"id": "rid"})
    assert daemon.state_mgr.get_tombstone(rel) is None


def test_resurrection_guard_recycles_matching_lingering_file(monkeypatch, daemon):
    """A local file matching a remote-deletion tombstone is recycled, not uploaded."""
    from odsc.quickxorhash import quickxorhash_file

    rel = "file.txt"
    local = daemon.config.sync_directory / rel
    local.write_bytes(b"the deleted bytes")
    h = quickxorhash_file(local)
    daemon.state_mgr.add_tombstone(rel, origin="remote", quick_xor=h)

    uploaded = []
    daemon.client = types.SimpleNamespace(upload_file=lambda *a, **k: uploaded.append(a) or {})
    trashed = []
    monkeypatch.setattr(daemon_module, "send2trash", lambda p: trashed.append(p))

    daemon._upload_file(rel, {"path": local, "mtime": 5.0, "size": local.stat().st_size})

    assert uploaded == []           # NOT re-uploaded (no resurrection)
    assert len(trashed) == 1        # recycled instead
    assert daemon.state_mgr.get_tombstone(rel) is None  # reconciled


def test_resurrection_guard_uploads_user_replaced_file(monkeypatch, daemon):
    """A NEW user file at a tombstoned path must upload (and clear the tombstone),
    never be trashed."""
    rel = "file.txt"
    local = daemon.config.sync_directory / rel
    local.write_bytes(b"brand new user content")
    daemon.state_mgr.add_tombstone(rel, origin="remote", quick_xor="OLD_DELETED_HASH")

    uploaded = []
    daemon.client = types.SimpleNamespace(
        upload_file=lambda *a, **k: uploaded.append(a) or {"eTag": "e2"}
    )
    trashed = []
    monkeypatch.setattr(daemon_module, "send2trash", lambda p: trashed.append(p))

    daemon._upload_file(rel, {"path": local, "mtime": 5.0, "size": local.stat().st_size})

    assert len(uploaded) == 1                # user's new file is uploaded
    assert trashed == []                     # never trashed
    assert daemon.state_mgr.get_tombstone(rel) is None  # tombstone cleared


def test_remote_file_reappearance_clears_tombstone(daemon):
    """A path that reappears on OneDrive clears its stale deletion tombstone."""
    rel = "file.txt"
    daemon.state_mgr.add_tombstone(rel, origin="remote", quick_xor="H")
    item = {
        "id": "rid", "name": "file.txt", "size": 3, "eTag": "e",
        "lastModifiedDateTime": "t",
        "parentReference": {"path": "/drive/root:"},
        "file": {"hashes": {"quickXorHash": "H2"}},
    }
    daemon._process_remote_file(item, daemon.config.sync_directory)
    assert daemon.state_mgr.get_tombstone(rel) is None


# --------------------------------------------------------------------------- #
# Offline move detection (server-side PATCH instead of upload+orphan)          #
# --------------------------------------------------------------------------- #

def test_offline_move_detected_and_applied(daemon):
    """A file moved while offline is PATCH-moved on OneDrive, not re-uploaded."""
    from odsc.quickxorhash import quickxorhash_file

    sync_dir = daemon.config.sync_directory
    # New local file at the destination path with known content.
    (sync_dir / "Photos").mkdir(parents=True, exist_ok=True)
    dst = sync_dir / "Photos" / "moved.jpg"
    dst.write_bytes(b"image bytes here")
    h = quickxorhash_file(dst)
    size = dst.stat().st_size

    # Source: a previously-synced remote file (same content) now absent locally.
    daemon.state_mgr.set_cache_entry("old.jpg", {"id": "ITEM1", "size": size, "eTag": "e", "quickXorHash": h})
    daemon.state_mgr.set_file_entry("old.jpg", mtime=1.0, size=size, metadata={"eTag": "e", "quickXorHash": h})

    moves = []
    daemon.client = types.SimpleNamespace(
        move_item=lambda item_id, new_name, new_parent: moves.append((item_id, new_name, new_parent)) or {"id": item_id}
    )

    local_files = {"Photos/moved.jpg": {"path": dst, "mtime": 2.0, "size": size}}
    daemon._detect_and_apply_moves(sync_dir, local_files)

    assert moves == [("ITEM1", "moved.jpg", "Photos")]   # PATCH move issued
    # State now tracks the new path, old path gone.
    assert daemon.state_mgr.get_file_entry("Photos/moved.jpg") != {}
    assert daemon.state_mgr.get_file_entry("old.jpg") == {}


def test_no_false_move_on_different_content(daemon):
    """A new local file whose content differs is not treated as a move."""
    from odsc.quickxorhash import quickxorhash_file

    sync_dir = daemon.config.sync_directory
    dst = sync_dir / "new.bin"
    dst.write_bytes(b"completely different content")
    size = dst.stat().st_size
    # A remote orphan of the SAME SIZE but different hash.
    daemon.state_mgr.set_cache_entry("old.bin", {"id": "ITEM2", "size": size, "eTag": "e", "quickXorHash": "DIFFERENT_HASH"})
    daemon.state_mgr.set_file_entry("old.bin", mtime=1.0, size=size, metadata={"eTag": "e", "quickXorHash": "DIFFERENT_HASH"})

    moves = []
    daemon.client = types.SimpleNamespace(move_item=lambda *a, **k: moves.append(a))

    daemon._detect_and_apply_moves(sync_dir, {"new.bin": {"path": dst, "mtime": 2.0, "size": size}})

    assert moves == []  # size matched but hash did not -> no move


def test_no_move_when_old_path_still_present_locally(daemon):
    """A copy (old path still local) must not be mis-detected as a move."""
    from odsc.quickxorhash import quickxorhash_file

    sync_dir = daemon.config.sync_directory
    old = sync_dir / "orig.txt"
    old.write_bytes(b"shared content")
    new = sync_dir / "copy.txt"
    new.write_bytes(b"shared content")
    h = quickxorhash_file(old)
    size = old.stat().st_size
    daemon.state_mgr.set_cache_entry("orig.txt", {"id": "ITEM3", "size": size, "eTag": "e", "quickXorHash": h})
    daemon.state_mgr.set_file_entry("orig.txt", mtime=1.0, size=size, metadata={"eTag": "e", "quickXorHash": h})

    moves = []
    daemon.client = types.SimpleNamespace(move_item=lambda *a, **k: moves.append(a))

    local_files = {
        "orig.txt": {"path": old, "mtime": 1.0, "size": size},   # source still present locally
        "copy.txt": {"path": new, "mtime": 2.0, "size": size},
    }
    daemon._detect_and_apply_moves(sync_dir, local_files)

    assert moves == []  # orig still present -> it's a copy, not a move


def test_remote_folder_cached_with_is_folder_flag(daemon):
    """A folder from the delta must be cached as a folder so the SAME cycle's
    folder reconciliation does not mistake it for a remote deletion."""
    item = {
        "id": "fid", "name": "_odsc_selftest", "folder": {},
        "parentReference": {"path": "/drive/root:"},
    }
    daemon._process_remote_folder(item, daemon.config.sync_directory)

    folders = daemon.state_mgr.all_remote_folders()
    assert "_odsc_selftest" in folders  # would be EXCLUDED before the fix
    assert daemon.state_mgr.get_cache_entry("_odsc_selftest") is not None


def test_prune_resolved_conflicts_clears_stale_records(daemon):
    """A conflict whose .conflict file is gone (e.g. removed while stopped) is cleared,
    while one whose .conflict file still exists is kept."""
    sync_dir = daemon.config.sync_directory
    # Stale: no .conflict file on disk.
    daemon.state_mgr.add_conflict("gone.txt", "gone.txt.conflict")
    # Active: .conflict file present.
    (sync_dir / "live.txt.conflict").write_text("remote version")
    daemon.state_mgr.add_conflict("live.txt", "live.txt.conflict")

    daemon._prune_resolved_conflicts(sync_dir)

    conflicts = daemon.state_mgr.all_conflicts()
    assert "gone.txt" not in conflicts   # stale record pruned
    assert "live.txt" in conflicts       # active conflict retained
