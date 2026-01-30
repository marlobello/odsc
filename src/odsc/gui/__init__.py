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
                
                # After 2 seconds, close splash and create main window
                GLib.timeout_add(2000, self._create_and_show_main_window)
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
        
        def _create_and_show_main_window(self):
            """Create and show main window after splash.
            
            Returns:
                False to stop timeout
            """
            # Close splash
            if self.splash:
                self.splash.close_splash()
                self.splash = None
            
            # Create and show main window
            self.window = OneDriveGUI(self)
            self.window.show_all()
            
            return False
    
    app = OneDriveApplication()
    app.run(None)
