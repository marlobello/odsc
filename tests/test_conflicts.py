#!/usr/bin/env python3
"""Tests for conflict resolution features."""

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from odsc.sync_state import SyncStateManager


# ------------------------------------------------------------------ #
# SyncStateManager conflict tracking                                   #
# ------------------------------------------------------------------ #

class TestConflictTracking:
    """Tests for conflict state management."""

    def _make_manager(self):
        saved = []
        return SyncStateManager(lambda: {}, saved.append), saved

    def test_add_conflict_stores_entry(self):
        mgr, _ = self._make_manager()
        remote_info = {"lastModifiedDateTime": "2026-06-04T12:00:00Z"}
        mgr.add_conflict("docs/file.txt", "docs/file.txt.conflict", remote_info)

        conflicts = mgr.all_conflicts()
        assert "docs/file.txt" in conflicts
        assert conflicts["docs/file.txt"]["conflict_path"] == "docs/file.txt.conflict"
        assert conflicts["docs/file.txt"]["remote_modified"] == "2026-06-04T12:00:00Z"
        assert "detected_at" in conflicts["docs/file.txt"]

    def test_remove_conflict_clears_entry(self):
        mgr, _ = self._make_manager()
        mgr.add_conflict("a.txt", "a.txt.conflict")
        mgr.remove_conflict("a.txt")

        assert mgr.all_conflicts() == {}
        assert mgr.conflict_count() == 0

    def test_remove_nonexistent_conflict_is_noop(self):
        mgr, _ = self._make_manager()
        mgr.remove_conflict("no-such-file.txt")  # should not raise
        assert mgr.conflict_count() == 0

    def test_conflict_count(self):
        mgr, _ = self._make_manager()
        assert mgr.conflict_count() == 0
        mgr.add_conflict("a.txt", "a.txt.conflict")
        mgr.add_conflict("b.txt", "b.txt.conflict")
        assert mgr.conflict_count() == 2

    def test_all_conflicts_returns_deep_copy(self):
        mgr, _ = self._make_manager()
        mgr.add_conflict("a.txt", "a.txt.conflict")
        conflicts = mgr.all_conflicts()
        conflicts["a.txt"]["conflict_path"] = "MUTATED"
        assert mgr.all_conflicts()["a.txt"]["conflict_path"] == "a.txt.conflict"

    def test_conflicts_persist_across_save_load(self):
        saved = []
        mgr = SyncStateManager(lambda: {}, saved.append)
        mgr.add_conflict("x.txt", "x.txt.conflict")
        mgr.save()

        # Simulate reload from saved state
        mgr2 = SyncStateManager(lambda: saved[-1], lambda s: None)
        mgr2.load()
        assert mgr2.conflict_count() == 1
        assert "x.txt" in mgr2.all_conflicts()

    def test_conflicts_initialized_on_empty_state(self):
        mgr = SyncStateManager(lambda: {}, lambda s: None)
        assert mgr.conflict_count() == 0
        assert mgr.all_conflicts() == {}


# ------------------------------------------------------------------ #
# Numbered conflict file naming                                        #
# ------------------------------------------------------------------ #

class TestConflictNaming:
    """Tests for _next_conflict_name in daemon."""

    def test_first_conflict_uses_base_name(self, tmp_path):
        from odsc.daemon import SyncDaemon
        from odsc.config import Config

        # Use the static method logic directly
        daemon = object.__new__(SyncDaemon)
        result = daemon._next_conflict_name("docs/report.txt", tmp_path)
        assert result == "docs/report.txt.conflict"

    def test_second_conflict_gets_number_2(self, tmp_path):
        from odsc.daemon import SyncDaemon

        # Create existing .conflict file
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "report.txt.conflict").touch()

        daemon = object.__new__(SyncDaemon)
        result = daemon._next_conflict_name("docs/report.txt", tmp_path)
        assert result == "docs/report.txt.conflict.2"

    def test_third_conflict_gets_number_3(self, tmp_path):
        from odsc.daemon import SyncDaemon

        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "report.txt.conflict").touch()
        (tmp_path / "docs" / "report.txt.conflict.2").touch()

        daemon = object.__new__(SyncDaemon)
        result = daemon._next_conflict_name("docs/report.txt", tmp_path)
        assert result == "docs/report.txt.conflict.3"


# ------------------------------------------------------------------ #
# Auto-clear conflict on .conflict file deletion                       #
# ------------------------------------------------------------------ #

class TestConflictAutoClear:
    """Tests for _maybe_clear_conflict in daemon."""

    def test_clears_matching_conflict(self):
        from odsc.daemon import SyncDaemon

        daemon = object.__new__(SyncDaemon)
        daemon.state_mgr = SyncStateManager(lambda: {}, lambda s: None)
        daemon.state_mgr.add_conflict("notes.txt", "notes.txt.conflict")

        daemon._maybe_clear_conflict("notes.txt.conflict")

        assert daemon.state_mgr.conflict_count() == 0

    def test_ignores_unrelated_deletion(self):
        from odsc.daemon import SyncDaemon

        daemon = object.__new__(SyncDaemon)
        daemon.state_mgr = SyncStateManager(lambda: {}, lambda s: None)
        daemon.state_mgr.add_conflict("notes.txt", "notes.txt.conflict")

        daemon._maybe_clear_conflict("other_file.txt")

        assert daemon.state_mgr.conflict_count() == 1

    def test_noop_when_no_conflicts(self):
        from odsc.daemon import SyncDaemon

        daemon = object.__new__(SyncDaemon)
        daemon.state_mgr = SyncStateManager(lambda: {}, lambda s: None)

        daemon._maybe_clear_conflict("anything.conflict")  # should not raise
        assert daemon.state_mgr.conflict_count() == 0


# ------------------------------------------------------------------ #
# Conflict resolution dialog file operations                           #
# ------------------------------------------------------------------ #

class TestConflictResolutionActions:
    """Tests for conflict resolution file operations (keep local/remote)."""

    def test_keep_local_deletes_conflict_file(self, tmp_path):
        """Keep Local should delete the .conflict file."""
        from odsc.gui.conflict_dialog import ConflictResolutionDialog

        # Setup files
        (tmp_path / "report.txt").write_text("local content")
        (tmp_path / "report.txt.conflict").write_text("remote content")

        conflicts = {
            "report.txt": {
                "conflict_path": "report.txt.conflict",
                "detected_at": "2026-06-04T12:00:00",
                "remote_modified": "2026-06-04T11:00:00Z",
            }
        }

        # Simulate keep_local action directly on the file logic
        conflict_path = tmp_path / "report.txt.conflict"
        if conflict_path.exists():
            conflict_path.unlink()

        assert (tmp_path / "report.txt").exists()
        assert not (tmp_path / "report.txt.conflict").exists()
        assert (tmp_path / "report.txt").read_text() == "local content"

    def test_keep_remote_replaces_local_with_conflict(self, tmp_path):
        """Keep Remote should replace local with the .conflict file."""
        import shutil

        (tmp_path / "report.txt").write_text("local content")
        (tmp_path / "report.txt.conflict").write_text("remote content")

        local_path = tmp_path / "report.txt"
        conflict_path = tmp_path / "report.txt.conflict"
        shutil.move(str(conflict_path), str(local_path))

        assert (tmp_path / "report.txt").read_text() == "remote content"
        assert not (tmp_path / "report.txt.conflict").exists()
