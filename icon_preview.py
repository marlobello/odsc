#!/usr/bin/env python3
"""
Icon Preview Tool for ODSC
Shows all icons with "emblem-" prefix
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

class IconPreviewWindow(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, title="All Emblem Icons Preview")
        self.set_border_width(20)
        self.set_default_size(1000, 800)
        
        # Main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.add(main_box)
        
        # Title
        title = Gtk.Label()
        title.set_markup("<big><b>All Emblem Icons</b></big>")
        main_box.pack_start(title, False, False, 0)
        
        # Instructions
        instructions = Gtk.Label()
        instructions.set_text("All icons with 'emblem-' prefix. Icon names are selectable for copying.")
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
        
        # Get all emblem icons
        emblem_icons = self.get_emblem_icons()
        
        # Info label
        info = Gtk.Label()
        info.set_markup(f"<b>Found {len(emblem_icons)} emblem icons</b>")
        grid_box.pack_start(info, False, False, 0)
        
        # Display icons
        self.add_icon_grid(grid_box, emblem_icons)
        
        # Close button
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", Gtk.main_quit)
        main_box.pack_start(close_btn, False, False, 0)
    
    def get_emblem_icons(self):
        """Get all icon names that start with 'emblem-' from the current theme."""
        icon_theme = Gtk.IconTheme.get_default()
        all_icons = icon_theme.list_icons(None)  # Get all icons
        
        # Filter for emblem- prefix
        emblem_icons = sorted([icon for icon in all_icons if icon.startswith('emblem-')])
        
        return emblem_icons
    
    def add_icon_grid(self, container, icon_names):
        """Add icons in a grid layout."""
        # Grid for icons
        grid = Gtk.Grid()
        grid.set_row_spacing(15)
        grid.set_column_spacing(20)
        container.pack_start(grid, False, False, 0)
        
        # Add icons in rows of 5
        for i, icon_name in enumerate(icon_names):
            row = i // 5
            col = i % 5
            
            # Icon + label box
            icon_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            icon_box.set_size_request(150, 110)
            
            # Try to load icon, show placeholder if not available
            icon = Gtk.Image()
            try:
                icon.set_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
                icon.set_pixel_size(48)
            except:
                # If icon doesn't render, show text
                icon.set_from_icon_name('image-missing', Gtk.IconSize.DIALOG)
            
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

def main():
    win = IconPreviewWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
