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
    from gi.repository import Gtk, Gio
    
    class OneDriveApplication(Gtk.Application):
        """GTK Application for OneDrive Sync Client."""
        
        def __init__(self):
            """Initialize application."""
            super().__init__(application_id="com.github.odsc",
                             flags=Gio.ApplicationFlags.FLAGS_NONE)
            self.window = None
        
        def do_activate(self):
            """Activate the application."""
            if not self.window:
                self.window = OneDriveGUI(self)
                self.window.show_all()
            self.window.present()
    
    app = OneDriveApplication()
    app.run(None)
