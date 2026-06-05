"""Conflict resolution dialog for ODSC GUI."""

import html
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango

logger = logging.getLogger(__name__)


def _format_size(size_bytes: int) -> str:
    """Format bytes into human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _format_mtime(timestamp: float) -> str:
    """Format a unix timestamp to a human-readable string."""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return "Unknown"


class ConflictResolutionDialog(Gtk.Dialog):
    """Dialog for resolving file conflicts one at a time."""

    # Resolution actions
    KEEP_LOCAL = "keep_local"
    KEEP_REMOTE = "keep_remote"
    KEEP_BOTH = "keep_both"
    SKIP = "skip"

    def __init__(self, parent, sync_dir: Path, conflicts: Dict[str, Dict[str, Any]]):
        """Initialize conflict resolution dialog.

        Args:
            parent: Parent window
            sync_dir: Sync directory root
            conflicts: Dict of original_path -> conflict info from state
        """
        Gtk.Dialog.__init__(
            self,
            title="Resolve Conflicts",
            transient_for=parent,
            flags=Gtk.DialogFlags.MODAL,
        )
        self.set_default_size(600, 450)
        self.set_border_width(12)

        self._sync_dir = sync_dir
        self._conflicts = list(conflicts.items())
        self._index = 0
        self._results = {}  # original_path -> action taken

        box = self.get_content_area()
        box.set_spacing(12)

        # Header
        self._header_label = Gtk.Label()
        self._header_label.set_halign(Gtk.Align.START)
        box.pack_start(self._header_label, False, False, 0)

        # File name
        self._filename_label = Gtk.Label()
        self._filename_label.set_halign(Gtk.Align.START)
        self._filename_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._filename_label.set_selectable(True)
        box.pack_start(self._filename_label, False, False, 0)

        # Separator
        box.pack_start(Gtk.Separator(), False, False, 4)

        # Two-column comparison
        grid = Gtk.Grid()
        grid.set_column_spacing(24)
        grid.set_row_spacing(8)
        grid.set_column_homogeneous(True)
        box.pack_start(grid, False, False, 0)

        # Column headers
        local_header = Gtk.Label()
        local_header.set_markup("<b>Local Version</b> (your edits)")
        local_header.set_halign(Gtk.Align.START)
        grid.attach(local_header, 0, 0, 1, 1)

        remote_header = Gtk.Label()
        remote_header.set_markup("<b>Remote Version</b> (from OneDrive)")
        remote_header.set_halign(Gtk.Align.START)
        grid.attach(remote_header, 1, 0, 1, 1)

        # Local metadata labels
        self._local_size_label = Gtk.Label()
        self._local_size_label.set_halign(Gtk.Align.START)
        grid.attach(self._local_size_label, 0, 1, 1, 1)

        self._local_modified_label = Gtk.Label()
        self._local_modified_label.set_halign(Gtk.Align.START)
        grid.attach(self._local_modified_label, 0, 2, 1, 1)

        # Remote metadata labels
        self._remote_size_label = Gtk.Label()
        self._remote_size_label.set_halign(Gtk.Align.START)
        grid.attach(self._remote_size_label, 1, 1, 1, 1)

        self._remote_modified_label = Gtk.Label()
        self._remote_modified_label.set_halign(Gtk.Align.START)
        grid.attach(self._remote_modified_label, 1, 2, 1, 1)

        # Separator
        box.pack_start(Gtk.Separator(), False, False, 4)

        # Action buttons
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_box.set_halign(Gtk.Align.CENTER)
        box.pack_start(action_box, False, False, 8)

        keep_local_btn = Gtk.Button(label="Keep Local")
        keep_local_btn.get_style_context().add_class("suggested-action")
        keep_local_btn.set_tooltip_text("Delete the remote copy; keep your local edits")
        keep_local_btn.connect("clicked", self._on_keep_local)
        action_box.pack_start(keep_local_btn, False, False, 0)

        keep_remote_btn = Gtk.Button(label="Keep Remote")
        keep_remote_btn.set_tooltip_text("Replace local with the remote version")
        keep_remote_btn.connect("clicked", self._on_keep_remote)
        action_box.pack_start(keep_remote_btn, False, False, 0)

        keep_both_btn = Gtk.Button(label="Keep Both")
        keep_both_btn.set_tooltip_text("Leave both files in place (resolve manually later)")
        keep_both_btn.connect("clicked", self._on_keep_both)
        action_box.pack_start(keep_both_btn, False, False, 0)

        skip_btn = Gtk.Button(label="Skip")
        skip_btn.set_tooltip_text("Skip this conflict for now")
        skip_btn.connect("clicked", self._on_skip)
        action_box.pack_start(skip_btn, False, False, 0)

        # Close button at the bottom
        self.add_button("Done", Gtk.ResponseType.CLOSE)

        self.show_all()
        self._show_current_conflict()

    @property
    def results(self) -> Dict[str, str]:
        """Return dict of original_path -> action taken."""
        return self._results

    def _show_current_conflict(self) -> None:
        """Populate the dialog with the current conflict's details."""
        if self._index >= len(self._conflicts):
            self._show_all_resolved()
            return

        original_path, info = self._conflicts[self._index]
        conflict_rel = info.get("conflict_path", "")
        remote_modified = info.get("remote_modified", "")

        total = len(self._conflicts)
        self._header_label.set_markup(
            f"<b>Conflict {self._index + 1} of {total}</b>"
        )
        self._filename_label.set_markup(
            f"<span font_family='monospace'>{html.escape(original_path)}</span>"
        )

        # Get local file metadata
        local_path = self._sync_dir / original_path
        if local_path.exists():
            try:
                stat = local_path.stat()
                self._local_size_label.set_text(f"Size: {_format_size(stat.st_size)}")
                self._local_modified_label.set_text(f"Modified: {_format_mtime(stat.st_mtime)}")
            except OSError:
                self._local_size_label.set_text("Size: unavailable")
                self._local_modified_label.set_text("Modified: unavailable")
        else:
            self._local_size_label.set_text("Size: file missing")
            self._local_modified_label.set_text("Modified: —")

        # Get remote (conflict) file metadata
        conflict_path = self._sync_dir / conflict_rel
        if conflict_path.exists():
            try:
                stat = conflict_path.stat()
                self._remote_size_label.set_text(f"Size: {_format_size(stat.st_size)}")
            except OSError:
                self._remote_size_label.set_text("Size: unavailable")
        else:
            self._remote_size_label.set_text("Size: file missing")

        if remote_modified:
            self._remote_modified_label.set_text(f"Modified: {remote_modified}")
        else:
            self._remote_modified_label.set_text("Modified: unknown")

    def _show_all_resolved(self) -> None:
        """Update UI when all conflicts have been addressed."""
        self._header_label.set_markup("<b>All conflicts addressed!</b>")
        self._filename_label.set_text("")
        self._local_size_label.set_text("")
        self._local_modified_label.set_text("")
        self._remote_size_label.set_text("")
        self._remote_modified_label.set_text("")

    def _advance(self) -> None:
        """Move to the next conflict."""
        self._index += 1
        self._show_current_conflict()

    def _on_keep_local(self, widget) -> None:
        """Keep local version, delete the .conflict file."""
        original_path, info = self._conflicts[self._index]
        conflict_rel = info.get("conflict_path", "")
        conflict_path = self._sync_dir / conflict_rel

        try:
            if conflict_path.exists():
                conflict_path.unlink()
                logger.info(f"Conflict resolved (kept local): {original_path}")
        except OSError as e:
            logger.error(f"Failed to delete conflict file: {e}")

        self._results[original_path] = self.KEEP_LOCAL
        self._advance()

    def _on_keep_remote(self, widget) -> None:
        """Keep remote version, replace local with the .conflict file."""
        original_path, info = self._conflicts[self._index]
        conflict_rel = info.get("conflict_path", "")
        local_path = self._sync_dir / original_path
        conflict_path = self._sync_dir / conflict_rel

        try:
            if conflict_path.exists():
                shutil.move(str(conflict_path), str(local_path))
                logger.info(f"Conflict resolved (kept remote): {original_path}")
            else:
                logger.warning(f"Conflict file missing, cannot resolve: {conflict_rel}")
        except OSError as e:
            logger.error(f"Failed to replace local with remote: {e}")

        self._results[original_path] = self.KEEP_REMOTE
        self._advance()

    def _on_keep_both(self, widget) -> None:
        """Keep both files in place — user resolves manually."""
        original_path, _ = self._conflicts[self._index]
        self._results[original_path] = self.KEEP_BOTH
        logger.info(f"Conflict kept both versions: {original_path}")
        self._advance()

    def _on_skip(self, widget) -> None:
        """Skip this conflict."""
        original_path, _ = self._conflicts[self._index]
        self._results[original_path] = self.SKIP
        self._advance()
