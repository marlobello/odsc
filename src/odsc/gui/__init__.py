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
        
        def do_activate(self):
            """Activate the application."""
            if not self.window:
                # Create main window first
                self.window = OneDriveGUI(self)
                self.window.show_all()
                
                # Show splash as modal overlay on top
                splash = SplashScreen()
                splash.set_transient_for(self.window)
                splash.set_modal(True)
                splash.show_all()
                
                # Auto-close splash after 2 seconds
                GLib.timeout_add(2000, splash.close_splash)
                
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
            
            return False
    
    app = OneDriveApplication()
    app.run(None)
