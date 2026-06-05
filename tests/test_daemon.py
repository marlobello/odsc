#!/usr/bin/env python3
"""Tests for daemon lifecycle and sync decision behavior."""

import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from odsc import daemon as daemon_module


class DummyConfig:
    """Small config stub for daemon tests."""

    def __init__(self, tmp_path: Path, sync_interval: int = 0):
        self.config_dir = tmp_path
        self.sync_directory = tmp_path / "sync"
        self.force_sync_path = tmp_path / ".force_sync"
        self.token_path = tmp_path / ".token"
        self.log_path = tmp_path / "odsc.log"
        self.log_level = "INFO"
        self.client_id = ""
        self.sync_interval = sync_interval
        self.download_chunk_size = 4096
        self.saved_states = []
        self.closed = False

    def load_state(self):
        return {}

    def save_state(self, state):
        self.saved_states.append(state)

    def load_token(self):
        return {"access_token": "token"}

    def close(self):
        self.closed = True


class ImmediateThread:
    """Thread stub that runs the target immediately."""

    def __init__(self, target, daemon=False, **kwargs):
        self._target = target
        self.daemon = daemon
        self.joined = False

    def start(self):
        self._target()

    def join(self, timeout=None):
        self.joined = True


@pytest.fixture(autouse=True)
def patch_signal_registration(monkeypatch):
    """Avoid mutating process-global signal handlers during tests."""
    monkeypatch.setattr(daemon_module.signal, "signal", lambda *args, **kwargs: None)


@pytest.fixture
def config(tmp_path):
    return DummyConfig(tmp_path)


def test_start_runs_headless_and_stops_cleanly(monkeypatch, config):
    """The daemon should monitor the sync dir and clean up in headless mode."""
    observer = Mock()
    monkeypatch.setattr(daemon_module, "Observer", lambda: observer)
    monkeypatch.setattr(daemon_module, "CommandServer", lambda *a, **kw: Mock())
    monkeypatch.setattr(daemon_module.threading, "Thread", ImmediateThread)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    daemon = daemon_module.SyncDaemon(config)
    monkeypatch.setattr(daemon, "initialize", lambda: True)
    monkeypatch.setattr(daemon, "_sync_loop", lambda: setattr(daemon, "_running", False))

    daemon.start()

    assert config.sync_directory.is_dir()
    observer.schedule.assert_called_once_with(daemon.event_handler, str(config.sync_directory), recursive=True)
    observer.start.assert_called_once()
    observer.stop.assert_called_once()
    observer.join.assert_called_once()
    assert daemon.system_tray is None
    assert daemon._gtk_mode is False
    assert config.closed is True


def test_start_falls_back_to_headless_when_tray_setup_fails(monkeypatch, config):
    """A tray initialization failure should not prevent the daemon from starting."""
    observer = Mock()
    monkeypatch.setattr(daemon_module, "Observer", lambda: observer)
    monkeypatch.setattr(daemon_module, "CommandServer", lambda *a, **kw: Mock())
    monkeypatch.setattr(daemon_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(daemon_module, "SYSTEM_TRAY_AVAILABLE", True)
    if not hasattr(daemon_module, "SystemTrayIndicator"):
        monkeypatch.setattr(daemon_module, "SystemTrayIndicator", None, raising=False)
    monkeypatch.setattr(
        daemon_module,
        "SystemTrayIndicator",
        Mock(side_effect=RuntimeError("tray unavailable")),
    )
    monkeypatch.setenv("DISPLAY", ":0")

    daemon = daemon_module.SyncDaemon(config)
    monkeypatch.setattr(daemon, "initialize", lambda: True)
    monkeypatch.setattr(daemon, "_sync_loop", lambda: setattr(daemon, "_running", False))

    daemon.start()

    assert daemon.system_tray is None
    assert daemon._gtk_mode is False
    observer.start.assert_called_once()
    observer.stop.assert_called_once()


def test_on_glib_signal_quits_main_loop(monkeypatch, config):
    """GLib signal handlers should defer main_quit onto an idle callback.

    Calling Gtk.main_quit() directly from a unix-signal source does not break
    Gtk.main(), so the handler must schedule it via GLib.idle_add instead.
    """
    gtk = types.SimpleNamespace(main_quit=Mock())
    marker = object()
    idle_add = Mock()
    monkeypatch.setattr(daemon_module, "Gtk", gtk)
    monkeypatch.setattr(
        daemon_module, "GLib",
        types.SimpleNamespace(SOURCE_REMOVE=marker, idle_add=idle_add),
    )

    daemon = daemon_module.SyncDaemon(config)
    daemon._running = True

    result = daemon._on_glib_signal(15)

    assert daemon._running is False
    # Quit is deferred via idle_add, not called directly.
    idle_add.assert_called_once_with(gtk.main_quit)
    gtk.main_quit.assert_not_called()
    assert result is marker


def test_sync_loop_recovers_after_exceptions(monkeypatch, config, caplog):
    """Unexpected loop errors should be logged and the next iteration should continue."""
    daemon = daemon_module.SyncDaemon(config)
    daemon._running = True
    daemon.event_handler = types.SimpleNamespace(
        get_pending_moves=Mock(return_value={}),
        get_pending_changes=Mock(side_effect=[RuntimeError("boom"), set()]),
    )
    update_calls = []

    monkeypatch.setattr(daemon, "_check_force_sync_signal", lambda: False)
    monkeypatch.setattr(daemon, "_should_do_periodic_sync", lambda: False)

    def fake_update_check():
        update_calls.append(True)
        daemon._running = False

    monkeypatch.setattr(daemon, "_check_for_updates", fake_update_check)

    daemon._sync_loop()

    assert daemon.event_handler.get_pending_changes.call_count == 2
    assert len(update_calls) == 1
    assert "Error in sync loop: boom" in caplog.text


def test_determine_sync_action_returns_conflict_when_both_sides_changed(config):
    """Conflicts should be detected when local and remote copies both changed."""
    daemon = daemon_module.SyncDaemon(config)

    action = daemon._determine_sync_action(
        "docs/report.txt",
        {"mtime": 2.0, "size": 50},
        {"eTag": "remote-new", "lastModifiedDateTime": "2024-02-01T00:00:00", "size": 50},
        {
            "mtime": 1.0,
            "size": 50,
            "downloaded": True,
            "eTag": "remote-old",
            "remote_modified": "2024-01-01T00:00:00",
        },
    )

    assert action == "conflict"


def test_determine_sync_action_downloads_when_only_remote_changed(config):
    """A remote-only change should download the newer remote copy."""
    daemon = daemon_module.SyncDaemon(config)

    action = daemon._determine_sync_action(
        "docs/report.txt",
        {"mtime": 1.0, "size": 50},
        {"eTag": "remote-new", "lastModifiedDateTime": "2024-02-01T00:00:00", "size": 50},
        {
            "mtime": 1.0,
            "size": 50,
            "downloaded": True,
            "eTag": "remote-old",
            "remote_modified": "2024-01-01T00:00:00",
        },
    )

    assert action == "download"


def test_sync_file_records_upload_errors(monkeypatch, config):
    """Failed uploads should persist the error in sync state for later recovery."""
    daemon = daemon_module.SyncDaemon(config)
    config.sync_directory.mkdir(parents=True)
    file_path = config.sync_directory / "notes.txt"
    file_path.write_text("local contents")
    daemon.client = types.SimpleNamespace(
        upload_file=Mock(side_effect=RuntimeError("network down"))
    )

    daemon._sync_file(file_path)

    entry = daemon.state_mgr.get_file_entry("notes.txt")
    assert entry["upload_error"] == "network down"
    assert entry["size"] == len("local contents")
    assert config.saved_states

