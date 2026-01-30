"""ODSC GUI package."""

from .main_window import OneDriveGUI
from .dialogs import DialogHelper, AuthInfoDialog, SettingsDialog
from .auth_handler import AuthCallbackHandler

__all__ = [
    'OneDriveGUI',
    'DialogHelper',
    'AuthInfoDialog',
    'SettingsDialog',
    'AuthCallbackHandler',
    'main',
]


def main():
    """Main entry point for GUI."""
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, Gio, GLib
    from .splash import SplashScreen
    
    class OneDriveApplication(Gtk.Application):
        """GTK Application for OneDrive Sync Client."""
        
        def __init__(self):
            """Initialize application."""
            # Use default flags which enables single-instance behavior
            super().__init__(application_id="com.github.odsc",
                             flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.window = None
            self.splash = None
        
        def do_activate(self):
            """Activate the application."""
            if not self.window:
                # Show splash screen immediately
                self.splash = SplashScreen()
                self.splash.show_all()
                
                # Start loading main window in background
                GLib.idle_add(self._load_main_window)
            else:
                # Window already exists - bring it to focus
                self.window.show_all()
                self.window.deiconify()  # Un-minimize if minimized
                
                # Set urgency hint to get window manager attention
                self.window.set_urgency_hint(True)
                
                # Try to present with timestamp
                self.window.present_with_time(Gtk.get_current_event_time())
                
                # Also try present() for good measure
                self.window.present()
                
                # Clear urgency hint after a moment
                GLib.timeout_add(100, lambda: self.window.set_urgency_hint(False) or False)
        
        def _load_main_window(self):
            """Load main window in background while splash is showing.
            
            Returns:
                False to stop idle callback
            """
            # Create main window (loads in background, not shown yet)
            self.window = OneDriveGUI(self)
            
            # Schedule splash to close after minimum display time (2 seconds)
            GLib.timeout_add(2000, self._show_main_window)
            
            return False
        
        def _show_main_window(self):
            """Hide splash and show main window.
            
            Returns:
                False to stop timeout
            """
            if self.splash:
                self.splash.close_splash()
                self.splash = None
            
            if self.window:
                self.window.show_all()
            
            return False
    
    app = OneDriveApplication()
    app.run(None)
