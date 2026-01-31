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
    """Splash screen / About dialog for ODSC."""
    
    def __init__(self, show_close_button=False):
        """Initialize splash screen.
        
        Args:
            show_close_button: If True, show close button (for About dialog mode)
        """
        # Use TOPLEVEL window that can be modal and transient
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_default_size(450, 350)
        self.set_resizable(False)
        
        self.show_close_button = show_close_button
        self.animation_active = not show_close_button  # Only animate on initial load
        self.dot_count = 0
        
        if show_close_button:
            # About dialog mode: show minimal decorations with close button
            self.set_decorated(True)
            self.set_deletable(True)
            self.set_title("About ODSC")
        else:
            # Splash mode: no decorations, can't be closed manually
            self.set_decorated(False)
            self.set_deletable(False)
        
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
        
        # Add subtitle
        subtitle_label = Gtk.Label()
        subtitle_label.set_markup('<span size="small" foreground="#666666">A lightweight Linux sync client for OneDrive</span>')
        subtitle_label.set_halign(Gtk.Align.CENTER)
        subtitle_label.set_line_wrap(True)
        vbox.pack_start(subtitle_label, False, False, 0)
        
        # Add loading dots (only visible in splash mode, not About dialog)
        if not show_close_button:
            self.dots_label = Gtk.Label()
            self.dots_label.set_markup('<span size="small" foreground="#999999"> </span>')
            self.dots_label.set_halign(Gtk.Align.CENTER)
            vbox.pack_start(self.dots_label, False, False, 0)
            
            # Start animation
            GLib.timeout_add(400, self._animate_dots)
        else:
            self.dots_label = None
        
        # Add links section
        links_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        links_box.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(links_box, False, False, 10)
        
        # GitHub link
        github_button = Gtk.LinkButton.new_with_label(
            "https://github.com/marlobello/odsc",
            "View on GitHub"
        )
        github_button.set_halign(Gtk.Align.CENTER)
        links_box.pack_start(github_button, False, False, 0)
        
        # License link
        license_button = Gtk.LinkButton.new_with_label(
            "https://github.com/marlobello/odsc/blob/main/LICENSE",
            "MIT License"
        )
        license_button.set_halign(Gtk.Align.CENTER)
        links_box.pack_start(license_button, False, False, 0)
        
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
    
    def _animate_dots(self) -> bool:
        """Animate the loading dots.
        
        Returns:
            True to continue animation, False to stop
        """
        if not self.animation_active or not self.dots_label:
            return False
        
        # Cycle through 0, 1, 2, 3 dots
        dots = "." * self.dot_count
        self.dots_label.set_markup(f'<span size="small" foreground="#999999">{dots}</span>')
        
        self.dot_count = (self.dot_count + 1) % 4
        return True
    
    def close_splash(self) -> bool:
        """Close the splash screen.
        
        Returns:
            False to stop the timeout
        """
        self.animation_active = False  # Stop animation
        self.hide()
        self.destroy()
        logger.debug("Splash screen closed")
        return False
