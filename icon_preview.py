#!/usr/bin/env python3
"""
Icon Preview Tool for ODSC
Shows different icon options for folder sync status
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

class IconPreviewWindow(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, title="ODSC Folder Status Icon Preview")
        self.set_border_width(20)
        self.set_default_size(900, 700)
        
        # Main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.add(main_box)
        
        # Title
        title = Gtk.Label()
        title.set_markup("<big><b>Choose Folder Status Icons</b></big>")
        main_box.pack_start(title, False, False, 0)
        
        # Instructions
        instructions = Gtk.Label()
        instructions.set_text("Hover over icons to see their names. Note the names you like for each status.")
        instructions.set_line_wrap(True)
        main_box.pack_start(instructions, False, False, 0)
        
        # Scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        main_box.pack_start(scrolled, True, True, 0)
        
        # Grid container
        grid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        grid_box.set_margin_start(10)
        grid_box.set_margin_end(10)
        grid_box.set_margin_top(10)
        grid_box.set_margin_bottom(10)
        scrolled.add(grid_box)
        
        # Add sections for each status
        self.add_status_section(grid_box, "All Files Synced", [
            'emblem-default',           # Current - green checkmark
            'emblem-ok',                # Alternative checkmark
            'emblem-downloads',         # Downloaded icon
            'folder-download',          # Folder with download
            'folder-saved-search',      # Folder with magnifying glass
            'emblem-synchronized',      # Sync complete
            'folder-visiting',          # Folder with person
            'user-home',                # Home icon
        ])
        
        self.add_status_section(grid_box, "Partially Synced", [
            'emblem-synchronizing',     # Current - sync arrows
            'view-refresh',             # Refresh arrows
            'emblem-system',            # System emblem
            'appointment-soon',         # Clock icon
            'mail-send-receive',        # Send/receive
            'network-idle',             # Network idle
            'folder-drag-accept',       # Folder with arrow
            'emblem-shared',            # Shared emblem
        ])
        
        self.add_status_section(grid_box, "Cloud-Only (No Local Files)", [
            'folder',                   # Current - plain folder
            'weather-overcast',         # Cloud
            'weather-few-clouds',       # Few clouds
            'network-server',           # Server icon
            'folder-remote',            # Remote folder
            'network-workgroup',        # Network workgroup
            'user-away',                # Away icon
            'folder-publicshare',       # Public share folder
        ])
        
        self.add_status_section(grid_box, "Empty Folder", [
            'folder',                   # Plain folder
            'folder-new',               # New folder
            'list-add',                 # Plus icon
            'document-new',             # New document
            'emblem-photos',            # Photos emblem
            'emblem-documents',         # Documents emblem
            'folder-visiting',          # Visiting folder
        ])
        
        # Current settings label
        current = Gtk.Label()
        current.set_markup(
            "\n<b>Current Settings:</b>\n"
            "All Synced: emblem-default\n"
            "Partial: emblem-synchronizing\n"
            "Cloud-only: folder\n"
            "Empty: (none)"
        )
        current.set_justify(Gtk.Justification.LEFT)
        main_box.pack_start(current, False, False, 0)
        
        # Close button
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", Gtk.main_quit)
        main_box.pack_start(close_btn, False, False, 0)
    
    def add_status_section(self, container, title, icon_names):
        """Add a section showing icons for a specific status."""
        # Section title
        section_label = Gtk.Label()
        section_label.set_markup(f"<b>{title}:</b>")
        section_label.set_halign(Gtk.Align.START)
        container.pack_start(section_label, False, False, 0)
        
        # Grid for icons
        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(20)
        container.pack_start(grid, False, False, 0)
        
        # Add icons in rows of 4
        for i, icon_name in enumerate(icon_names):
            row = i // 4
            col = i % 4
            
            # Icon + label box
            icon_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            icon_box.set_size_request(150, 100)
            
            # Icon
            icon = Gtk.Image()
            icon.set_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
            icon.set_pixel_size(48)
            icon.set_tooltip_text(icon_name)
            icon_box.pack_start(icon, True, True, 0)
            
            # Icon name label
            name_label = Gtk.Label()
            name_label.set_text(icon_name)
            name_label.set_line_wrap(True)
            name_label.set_max_width_chars(20)
            name_label.set_justify(Gtk.Justification.CENTER)
            name_label.set_selectable(True)  # Allow copying the name
            icon_box.pack_start(name_label, False, False, 0)
            
            # Add a frame for visual separation
            frame = Gtk.Frame()
            frame.set_shadow_type(Gtk.ShadowType.IN)
            frame.add(icon_box)
            
            grid.attach(frame, col, row, 1, 1)
        
        # Add separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        container.pack_start(separator, False, False, 10)

def main():
    win = IconPreviewWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
