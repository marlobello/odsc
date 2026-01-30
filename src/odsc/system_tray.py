#!/usr/bin/env python3
"""System tray indicator for ODSC daemon."""

import logging
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AppIndicator3', '0.1')
from gi.repository import Gtk, AppIndicator3, GLib

logger = logging.getLogger(__name__)


class SystemTrayIndicator:
    """System tray indicator for ODSC sync daemon."""
    
    def __init__(self, daemon=None):
        """Initialize system tray indicator.
        
        Args:
            daemon: Reference to SyncDaemon instance for status updates
        """
        self.daemon = daemon
        self.indicator = None
        self.status_item = None
        self._setup_indicator()
    
    def _setup_indicator(self):
        """Setup the AppIndicator."""
        # Find icon path
        icon_path = self._find_icon_path()
        
        # Create AppIndicator
        self.indicator = AppIndicator3.Indicator.new(
            "odsc-sync",
            icon_path if icon_path else "cloud-symbolic",
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("OneDrive Sync Client")
        
        # Create menu
        menu = self._create_menu()
        self.indicator.set_menu(menu)
        
        logger.info("System tray indicator initialized")
    
    def _find_icon_path(self) -> Optional[str]:
        """Find the ODSC icon file.
        
        Returns:
            Path to icon file or None
        """
        # Try common locations
        possible_paths = [
            Path(__file__).parent.parent.parent / "desktop" / "odsc.png",
            Path("/usr/share/pixmaps/odsc.png"),
            Path.home() / ".local/share/icons/odsc.png",
            Path("/usr/local/share/pixmaps/odsc.png"),
        ]
        
        for path in possible_paths:
            if path.exists():
                logger.debug(f"Found icon at: {path}")
                return str(path)
        
        logger.warning("ODSC icon not found, using fallback")
        return None
    
    def _create_menu(self) -> Gtk.Menu:
        """Create the tray menu.
        
        Returns:
            Gtk.Menu object
        """
        menu = Gtk.Menu()
        
        # Status item (non-clickable label)
        self.status_item = Gtk.MenuItem(label="Status: Running")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)
        
        # Separator
        separator1 = Gtk.SeparatorMenuItem()
        menu.append(separator1)
        
        # Open GUI
        gui_item = Gtk.MenuItem(label="Open GUI")
        gui_item.connect("activate", self._on_open_gui)
        menu.append(gui_item)
        
        # Separator
        separator2 = Gtk.SeparatorMenuItem()
        menu.append(separator2)
        
        # Stop Service
        stop_item = Gtk.MenuItem(label="Stop Sync Service")
        stop_item.connect("activate", self._on_stop_service)
        menu.append(stop_item)
        
        # Separator
        separator3 = Gtk.SeparatorMenuItem()
        menu.append(separator3)
        
        # About
        about_item = Gtk.MenuItem(label="About ODSC")
        about_item.connect("activate", self._on_about)
        menu.append(about_item)
        
        menu.show_all()
        return menu
    
    def update_status(self, status: str):
        """Update the status label.
        
        Args:
            status: Status text to display
        """
        if self.status_item:
            GLib.idle_add(self._update_status_label, status)
    
    def _update_status_label(self, status: str):
        """Update status label on GTK main thread.
        
        Args:
            status: Status text
        """
        if self.status_item:
            self.status_item.set_label(f"Status: {status}")
        return False
    
    def _on_open_gui(self, widget):
        """Handle Open GUI menu item."""
        try:
            # Try to launch or focus the GUI
            subprocess.Popen(
                ['odsc-gui'],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.info("Launched ODSC GUI")
        except Exception as e:
            logger.error(f"Failed to launch GUI: {e}")
    
    def _on_stop_service(self, widget):
        """Handle Stop Service menu item."""
        try:
            # Use systemctl to stop the service
            result = subprocess.run(
                ['systemctl', '--user', 'stop', 'odsc.service'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                logger.info("Service stop requested via system tray")
                # The service will terminate, taking the indicator with it
            else:
                logger.error(f"Failed to stop service: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            logger.error("Timeout stopping service")
        except FileNotFoundError:
            logger.warning("systemctl not found, terminating daemon directly")
            # If systemctl isn't available, terminate the daemon
            if self.daemon:
                self.daemon.stop()
        except Exception as e:
            logger.error(f"Error stopping service: {e}")
    
    def _on_about(self, widget):
        """Handle About menu item."""
        try:
            # Open GitHub page in browser
            webbrowser.open('https://github.com/marlobello/odsc')
            logger.info("Opened GitHub page")
        except Exception as e:
            logger.error(f"Failed to open browser: {e}")
    
    def run(self):
        """Run the GTK main loop (blocking)."""
        logger.info("Starting system tray indicator main loop")
        Gtk.main()
    
    def quit(self):
        """Quit the GTK main loop."""
        logger.info("Stopping system tray indicator")
        Gtk.main_quit()
