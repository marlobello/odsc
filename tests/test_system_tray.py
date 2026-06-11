#!/usr/bin/env python3
"""Tests for the system tray indicator."""

import importlib
import sys
import types
from unittest.mock import Mock

import pytest


@pytest.fixture
def tray_module(monkeypatch):
    """Import the system tray module with fake GI bindings."""
    watch_id = 42
    state = {
        "indicator_instances": [],
        "idle_calls": [],
    }

    class FakeIndicator:
        def __init__(self, name, icon, category):
            self.name = name
            self.icon = icon
            self.category = category
            self.title = None
            self.menu = None
            self.icon_theme_path = None
            self.status_calls = []

        @classmethod
        def new(cls, name, icon, category):
            indicator = cls(name, icon, category)
            state["indicator_instances"].append(indicator)
            return indicator

        def set_title(self, title):
            self.title = title

        def set_icon_theme_path(self, path):
            self.icon_theme_path = path

        def set_menu(self, menu):
            self.menu = menu

        def set_status(self, status):
            self.status_calls.append(status)

    class FakeMenu:
        def __init__(self):
            self.items = []
            self.shown = False

        def append(self, item):
            self.items.append(item)

        def show_all(self):
            self.shown = True

    class FakeMenuItem:
        def __init__(self, label=None):
            self.label = label
            self.sensitive = True
            self.connections = []

        def set_sensitive(self, value):
            self.sensitive = value

        def connect(self, signal_name, callback):
            self.connections.append((signal_name, callback))

        def set_label(self, label):
            self.label = label

    class FakeSeparatorMenuItem(FakeMenuItem):
        def __init__(self):
            super().__init__(label=None)

    class FakeIconTheme:
        @staticmethod
        def get_default():
            return types.SimpleNamespace(has_icon=lambda icon_name: False)

    def idle_add(func, *args):
        state["idle_calls"].append((func, args))
        return func(*args)

    Gtk = types.SimpleNamespace(
        Menu=FakeMenu,
        MenuItem=FakeMenuItem,
        SeparatorMenuItem=FakeSeparatorMenuItem,
        IconTheme=FakeIconTheme,
        main=Mock(),
        main_quit=Mock(),
    )
    AppIndicator3 = types.SimpleNamespace(
        Indicator=FakeIndicator,
        IndicatorCategory=types.SimpleNamespace(APPLICATION_STATUS="application-status"),
        IndicatorStatus=types.SimpleNamespace(ACTIVE="active"),
    )
    GLib = types.SimpleNamespace(idle_add=Mock(side_effect=idle_add))
    Gio = types.SimpleNamespace(
        BusType=types.SimpleNamespace(SESSION="session"),
        BusNameWatcherFlags=types.SimpleNamespace(NONE=0),
        bus_watch_name=Mock(return_value=watch_id),
        bus_unwatch_name=Mock(),
    )

    gi = types.ModuleType("gi")
    gi.require_version = lambda *args, **kwargs: None
    repository = types.ModuleType("gi.repository")
    repository.Gtk = Gtk
    repository.AppIndicator3 = AppIndicator3
    repository.GLib = GLib
    repository.Gio = Gio

    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    monkeypatch.delitem(sys.modules, "odsc.system_tray", raising=False)

    import odsc.system_tray as module

    module = importlib.reload(module)
    return module, state, Gtk, GLib, Gio


def test_create_menu_builds_expected_items_and_activates_indicator(tray_module):
    """Tray setup should build the menu and optimistically activate the icon."""
    module, state, _gtk, _glib, _gio = tray_module

    tray = module.SystemTrayIndicator()
    menu = tray.indicator.menu

    assert menu.shown is True
    assert menu.items[0].label == "Status: Running"
    assert menu.items[0].sensitive is False
    assert menu.items[2].label == "Open GUI"
    assert menu.items[4].label == "Stop Sync Service"
    assert menu.items[6].label == "About ODSC"
    assert tray.indicator.status_calls == ["active"]


def test_start_watching_registers_once_and_quit_unwatches(tray_module):
    """Bus watching should be idempotent and cleaned up on quit."""
    module, _state, gtk, _glib, gio = tray_module
    tray = module.SystemTrayIndicator()

    tray.start_watching()
    tray.start_watching()
    tray.quit()

    gio.bus_watch_name.assert_called_once()
    gio.bus_unwatch_name.assert_called_once_with(42)
    gtk.main_quit.assert_called_once()
    assert tray._watcher_watch_id is None


def test_watcher_appeared_reactivates_indicator(tray_module):
    """When the watcher appears, the tray should schedule activation on the main loop."""
    module, state, _gtk, glib, _gio = tray_module
    tray = module.SystemTrayIndicator()
    tray.indicator.status_calls.clear()

    tray._on_watcher_appeared(None, "org.kde.StatusNotifierWatcher", ":1.1")

    assert glib.idle_add.called
    assert state["idle_calls"][-1][0] == tray._activate_indicator
    assert tray.indicator.status_calls == ["active"]


def test_update_status_schedules_label_update(tray_module):
    """Status updates should be routed through GLib idle callbacks."""
    module, _state, _gtk, glib, _gio = tray_module
    tray = module.SystemTrayIndicator()

    tray.update_status("Paused")

def _install_fake_gi(monkeypatch, available_indicator):
    """Install fake `gi`/`gi.repository` modules exposing one indicator namespace.

    `available_indicator` is the namespace name that should resolve successfully
    ("AppIndicator3" or "AyatanaAppIndicator3"); `gi.require_version` raises
    ValueError for any other indicator namespace, mirroring real GI behaviour.
    """
    fake_indicator = types.SimpleNamespace(
        Indicator=object,
        IndicatorCategory=types.SimpleNamespace(APPLICATION_STATUS="application-status"),
        IndicatorStatus=types.SimpleNamespace(ACTIVE="active"),
    )

    def require_version(namespace, version):
        if namespace in ("AppIndicator3", "AyatanaAppIndicator3") and namespace != available_indicator:
            raise ValueError("Namespace %s not available" % namespace)

    gi = types.ModuleType("gi")
    gi.require_version = require_version
    repository = types.ModuleType("gi.repository")
    repository.Gtk = types.SimpleNamespace()
    repository.GLib = types.SimpleNamespace(idle_add=Mock())
    repository.Gio = types.SimpleNamespace()
    setattr(repository, available_indicator, fake_indicator)

    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    monkeypatch.delitem(sys.modules, "odsc.system_tray", raising=False)

    import odsc.system_tray as module
    return importlib.reload(module), fake_indicator


def test_tray_uses_legacy_appindicator_when_available(monkeypatch):
    """The module binds the alias to AppIndicator3 when that namespace is present."""
    module, fake_indicator = _install_fake_gi(monkeypatch, "AppIndicator3")
    assert module.AppIndicator is fake_indicator


def test_tray_falls_back_to_ayatana_appindicator(monkeypatch):
    """When AppIndicator3 is unavailable, the module falls back to Ayatana."""
    module, fake_indicator = _install_fake_gi(monkeypatch, "AyatanaAppIndicator3")
    assert module.AppIndicator is fake_indicator

