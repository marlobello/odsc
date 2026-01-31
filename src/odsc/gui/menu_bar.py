"""Menu bar creation and handlers for ODSC GUI."""

import logging
import threading
import webbrowser
import socketserver

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from ..onedrive_client import OneDriveClient
from .dialogs import DialogHelper, AuthInfoDialog, SettingsDialog
from .auth_handler import AuthCallbackHandler

logger = logging.getLogger(__name__)


class MenuBarMixin:
    """Mixin for menu bar creation and handlers."""
    
    def _create_menubar(self) -> Gtk.MenuBar:
        """Create menu bar.
        
        Returns:
            MenuBar widget
        """
        menubar = Gtk.MenuBar()
        
        # Authentication menu
        auth_menu = Gtk.Menu()
        auth_item = Gtk.MenuItem(label="Authentication")
        auth_item.set_submenu(auth_menu)
        
        # Login menu item
        self.login_menu_item = Gtk.MenuItem(label="Login")
        self.login_menu_item.connect("activate", self._on_login_clicked)
        auth_menu.append(self.login_menu_item)
        
        # Logout menu item
        self.logout_menu_item = Gtk.MenuItem(label="Logout")
        self.logout_menu_item.connect("activate", self._on_logout_clicked)
        auth_menu.append(self.logout_menu_item)
        
        # Separator
        auth_menu.append(Gtk.SeparatorMenuItem())
        
        # Authentication Info menu item
        auth_info_item = Gtk.MenuItem(label="Authentication Info...")
        auth_info_item.connect("activate", self._on_auth_info_clicked)
        auth_menu.append(auth_info_item)
        
        menubar.append(auth_item)
        
        # Settings menu
        settings_menu = Gtk.Menu()
        settings_item = Gtk.MenuItem(label="Settings")
        settings_item.set_submenu(settings_menu)
        
        settings_dialog_item = Gtk.MenuItem(label="Preferences...")
        settings_dialog_item.connect("activate", self._on_settings_clicked)
        settings_menu.append(settings_dialog_item)
        
        settings_menu.append(Gtk.SeparatorMenuItem())
        
        force_sync_item = Gtk.MenuItem(label="Force Sync Now")
        force_sync_item.connect("activate", self._on_force_sync_clicked)
        settings_menu.append(force_sync_item)
        
        menubar.append(settings_item)
        
        # Help menu
        help_menu = Gtk.Menu()
        help_item = Gtk.MenuItem(label="Help")
        help_item.set_submenu(help_menu)
        
        self.toggle_log_item = Gtk.CheckMenuItem(label="Show Log Panel")
        self.toggle_log_item.set_active(False)
        self.toggle_log_item.connect("toggled", self._on_toggle_log_panel)
        help_menu.append(self.toggle_log_item)
        
        help_menu.append(Gtk.SeparatorMenuItem())
        
        about_item = Gtk.MenuItem(label="About")
        about_item.connect("activate", self._on_about_clicked)
        help_menu.append(about_item)
        
        menubar.append(help_item)
        
        return menubar
    
    def _update_auth_menu_state(self) -> None:
        """Update authentication menu items based on current auth state."""
        is_authenticated = self.client is not None
        
        if self.login_menu_item:
            self.login_menu_item.set_sensitive(not is_authenticated)
        if self.logout_menu_item:
            self.logout_menu_item.set_sensitive(is_authenticated)
    
    def _on_login_clicked(self, widget) -> None:
        """Handle Login menu item click."""
        self._authenticate()
    
    def _on_logout_clicked(self, widget) -> None:
        """Handle Logout menu item click."""
        self._logout()
    
    def _on_auth_info_clicked(self, widget) -> None:
        """Handle Authentication Info menu item click."""
        dialog = AuthInfoDialog(self, self.config, self.client)
        dialog.run()
        dialog.destroy()
    
    def _authenticate(self) -> None:
        """Perform OneDrive authentication."""
        client_id = self.config.client_id or None
        
        logger.info("=== Starting Authentication Flow ===")
        logger.info(f"Using client_id: {client_id if client_id else 'DEFAULT'}")
        
        temp_client = OneDriveClient(client_id)
        auth_url = temp_client.get_auth_url()
        
        logger.info(f"Opening browser for authentication")
        webbrowser.open(auth_url)
        
        def wait_for_callback():
            try:
                logger.info("Starting local callback server on localhost:8080")
                with socketserver.TCPServer(("127.0.0.1", 8080), AuthCallbackHandler) as httpd:
                    httpd.timeout = 300
                    logger.debug("Waiting for OAuth callback...")
                    httpd.handle_request()
                    
                    if AuthCallbackHandler.auth_code:
                        if AuthCallbackHandler.state:
                            if not temp_client.validate_state(AuthCallbackHandler.state):
                                logger.error("State validation failed")
                                GLib.idle_add(self._show_error, 
                                            "Authentication failed:\nInvalid state parameter (possible CSRF attack)")
                                return
                        else:
                            logger.error("No state parameter received")
                            GLib.idle_add(self._show_error, 
                                        "Authentication failed:\nNo state parameter received")
                            return
                        
                        logger.info("Received authorization code from callback")
                        try:
                            token_data = temp_client.exchange_code(AuthCallbackHandler.auth_code)
                            self.config.save_token(token_data)
                            self.client = temp_client
                            logger.info("Authentication successful!")
                            
                            GLib.idle_add(self._on_auth_success)
                        except Exception as e:
                            logger.error(f"Auth failed: {e}", exc_info=True)
                            GLib.idle_add(self._show_error, f"Authentication failed: {e}")
                    else:
                        logger.warning("No authorization code received in callback")
            except OSError as e:
                logger.error(f"Socket error: {e}", exc_info=True)
                if e.errno == 98:
                    GLib.idle_add(self._show_error, "Port 8080 is already in use. Please close other applications using this port.")
                else:
                    GLib.idle_add(self._show_error, f"Network error: {e}")
        
        thread = threading.Thread(target=wait_for_callback, daemon=True)
        thread.start()
        
        self._update_status("Waiting for authentication...")
        logger.info("Authentication flow initiated, waiting for user...")
    
    def _on_auth_success(self) -> None:
        """Handle successful authentication."""
        self._update_status("Authentication successful!")
        self._update_auth_menu_state()
        self._load_remote_files()
    
    def _logout(self) -> None:
        """Log out and clear authentication."""
        self.config.token_path.unlink(missing_ok=True)
        self.client = None
        self.file_store.clear()
        
        self.keep_local_button.set_sensitive(False)
        self.remove_local_button.set_sensitive(False)
        self._update_status("Logged out successfully")
        self._update_auth_menu_state()
        
        DialogHelper.show_info(self, "Logged Out", "You have been logged out successfully.")
    
    def _on_settings_clicked(self, widget) -> None:
        """Handle settings button click."""
        dialog = SettingsDialog(self, self.config)
        dialog.run()
        dialog.destroy()
    
    def _on_force_sync_clicked(self, widget) -> None:
        """Handle force sync menu item click."""
        try:
            self.config.force_sync_path.touch()
            logger.info("Force sync signal created")
            
            DialogHelper.show_info(
                self, 
                "Sync Requested", 
                "The daemon has been signaled to perform a sync operation.",
                "The sync will begin within a few seconds if the daemon is running."
            )
        except Exception as e:
            logger.error(f"Failed to create force sync signal: {e}")
            DialogHelper.show_error(
                self,
                "Force Sync Failed",
                f"Could not signal the daemon: {e}"
            )
    
    def _on_about_clicked(self, widget) -> None:
        """Handle About menu item click."""
        try:
            from .splash import SplashScreen
            splash = SplashScreen(show_close_button=True)
            splash.set_transient_for(self)
            splash.set_modal(True)
            splash.show_all()
            # No auto-close timeout - user closes manually via X button
        except Exception as e:
            logger.error(f"Error showing about dialog: {e}")
