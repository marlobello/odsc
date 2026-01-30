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
    
    splash_window = None
    main_window_ref = [None]  # Use list to allow modification in nested function
    
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
            nonlocal splash_window, main_window_ref
            
            if not self.window:
                # Show splash screen immediately
                splash_window = SplashScreen()
                splash_window.show_all()
                
                # After 2 seconds, close splash and create main window
                def create_main():
                    try:
                        # Close splash
                        if splash_window:
                            splash_window.close_splash()
                        
                        # Create and show main window
                        self.window = OneDriveGUI(self)
                        self.window.show_all()
                        main_window_ref[0] = self.window
                        
                    except Exception as e:
                        import traceback
                        print(f"Error creating main window: {e}")
                        traceback.print_exc()
                    
                    return False
                
                GLib.timeout_add(2000, create_main)
                
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
