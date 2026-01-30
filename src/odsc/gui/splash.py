#!/usr/bin/env python3
"""Splash screen for ODSC GUI."""

import logging
from pathlib import Path
from typing import Optional

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

logger = logging.getLogger(__name__)


class SplashScreen(Gtk.Window):
    """Splash screen window for ODSC."""
    
    def __init__(self):
        """Initialize splash screen."""
        # Use TOPLEVEL instead of POPUP to avoid positioning warnings
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_default_size(400, 300)
        self.set_decorated(False)  # No window decorations
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)  # Don't show in taskbar
        self.set_skip_pager_hint(True)  # Don't show in pager
        self.set_type_hint(Gdk.WindowTypeHint.SPLASHSCREEN)  # Mark as splash screen
        
        # Create main container
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        vbox.set_border_width(30)
        self.add(vbox)
        
        # Add logo
        logo_image = self._create_logo()
        if logo_image:
            vbox.pack_start(logo_image, True, True, 0)
        
        # Add title label
        title_label = Gtk.Label()
        title_label.set_markup('<span size="x-large" weight="bold">OneDrive Sync Client</span>')
        title_label.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(title_label, False, False, 0)
        
        # Add version/subtitle
        subtitle_label = Gtk.Label()
        subtitle_label.set_markup('<span size="small" foreground="#666666">Loading...</span>')
        subtitle_label.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(subtitle_label, False, False, 0)
        
        # Add spinner
        spinner = Gtk.Spinner()
        spinner.set_size_request(32, 32)
        spinner.start()
        vbox.pack_start(spinner, False, False, 10)
        
        # Set background color
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            window {
                background-color: #ffffff;
                border: 2px solid #e0e0e0;
                border-radius: 10px;
            }
        """)
        
        style_context = self.get_style_context()
        style_context.add_provider(
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    
    def _create_logo(self) -> Optional[Gtk.Image]:
        """Create logo image widget.
        
        Returns:
            Gtk.Image or None if logo not found
        """
        # Try to find logo file - check from package location and system locations
        possible_paths = [
            # From installed package location
            Path(__file__).parent.parent.parent.parent / "desktop" / "odsc.png",
            # From development location
            Path(__file__).resolve().parents[3] / "desktop" / "odsc.png",
            # System installations
            Path("/usr/share/pixmaps/odsc.png"),
            Path("/usr/local/share/pixmaps/odsc.png"),
            Path.home() / ".local/share/icons/odsc.png",
            Path.home() / ".local/share/pixmaps/odsc.png",
        ]
        
        for path in possible_paths:
            if path.exists():
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        str(path),
                        128,  # width
                        128,  # height
                        True  # preserve aspect ratio
                    )
                    image = Gtk.Image.new_from_pixbuf(pixbuf)
                    logger.info(f"Loaded splash logo from: {path}")
                    return image
                except Exception as e:
                    logger.warning(f"Could not load logo from {path}: {e}")
        
        # Try loading from icon theme as fallback
        try:
            icon_theme = Gtk.IconTheme.get_default()
            if icon_theme and icon_theme.has_icon('odsc'):
                pixbuf = icon_theme.load_icon('odsc', 128, 0)
                if pixbuf:
                    image = Gtk.Image.new_from_pixbuf(pixbuf)
                    logger.info("Loaded splash logo from icon theme")
                    return image
        except Exception as e:
            logger.debug(f"Could not load from icon theme: {e}")
        
        logger.warning("Could not find ODSC logo for splash screen - splash will show without logo")
        return None
    
    def close_splash(self) -> bool:
        """Close the splash screen.
        
        Returns:
            False to stop the timeout
        """
        self.hide()
        self.destroy()
        logger.debug("Splash screen closed")
        return False
