#!/usr/bin/env python3
"""GNOME GTK GUI for ODSC."""

import logging
import threading
import webbrowser
from pathlib import Path
from typing import Optional, List, Dict, Any
import http.server
import socketserver
from urllib.parse import urlparse, parse_qs

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk

from .config import Config
from .onedrive_client import OneDriveClient


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AuthCallbackHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler for OAuth callback."""
    
    auth_code = None
    
    def do_GET(self):
        """Handle GET request for OAuth callback."""
        parsed = urlparse(self.path)
        if parsed.path == '/':
            params = parse_qs(parsed.query)
            if 'code' in params:
                AuthCallbackHandler.auth_code = params['code'][0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authentication successful!</h1>"
                                b"<p>You can close this window now.</p></body></html>")
            else:
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authentication failed!</h1></body></html>")
    
    def log_message(self, format, *args):
        """Suppress log messages."""
        pass


class OneDriveGUI(Gtk.Window):
    """Main GNOME GUI window for OneDrive Sync Client."""
    
    def __init__(self):
        """Initialize GUI."""
        Gtk.Window.__init__(self, title="OneDrive Sync Client")
        self.set_default_size(800, 600)
        self.set_border_width(10)
        
        self.config = Config()
        self.client: Optional[OneDriveClient] = None
        self.remote_files: List[Dict[str, Any]] = []
        
        # Initialize client if authenticated
        if self.config.load_token():
            self._init_client()
        
        self._build_ui()
        self.connect("destroy", Gtk.main_quit)
    
    def _init_client(self) -> bool:
        """Initialize OneDrive client.
        
        Returns:
            True if successful
        """
        client_id = self.config.client_id
        if not client_id:
            return False
        
        token_data = self.config.load_token()
        self.client = OneDriveClient(client_id, token_data)
        return True
    
    def _build_ui(self) -> None:
        """Build the user interface."""
        # Main vertical box
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add(vbox)
        
        # Toolbar
        toolbar = self._create_toolbar()
        vbox.pack_start(toolbar, False, False, 0)
        
        # Status bar
        self.status_label = Gtk.Label()
        self.status_label.set_markup("<i>Status: Ready</i>")
        self.status_label.set_halign(Gtk.Align.START)
        vbox.pack_start(self.status_label, False, False, 0)
        
        # Main content area with scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        vbox.pack_start(scrolled, True, True, 0)
        
        # TreeView for file list
        self.file_store = Gtk.ListStore(str, str, str, bool, str)  # name, size, modified, local, id
        self.file_tree = Gtk.TreeView(model=self.file_store)
        
        # Columns
        renderer_text = Gtk.CellRendererText()
        
        column_name = Gtk.TreeViewColumn("Name", renderer_text, text=0)
        column_name.set_resizable(True)
        column_name.set_min_width(300)
        self.file_tree.append_column(column_name)
        
        column_size = Gtk.TreeViewColumn("Size", renderer_text, text=1)
        column_size.set_resizable(True)
        self.file_tree.append_column(column_size)
        
        column_modified = Gtk.TreeViewColumn("Modified", renderer_text, text=2)
        column_modified.set_resizable(True)
        self.file_tree.append_column(column_modified)
        
        renderer_toggle = Gtk.CellRendererToggle()
        column_local = Gtk.TreeViewColumn("Local", renderer_toggle, active=3)
        self.file_tree.append_column(column_local)
        
        scrolled.add(self.file_tree)
        
        # Context menu for downloading
        self.file_tree.connect("button-press-event", self._on_tree_button_press)
        
        # Bottom button bar
        button_box = Gtk.Box(spacing=6)
        vbox.pack_start(button_box, False, False, 0)
        
        self.download_button = Gtk.Button(label="Download Selected")
        self.download_button.connect("clicked", self._on_download_clicked)
        self.download_button.set_sensitive(False)
        button_box.pack_start(self.download_button, False, False, 0)
        
        self.refresh_button = Gtk.Button(label="Refresh")
        self.refresh_button.connect("clicked", self._on_refresh_clicked)
        button_box.pack_start(self.refresh_button, False, False, 0)
        
        # Load files if authenticated
        if self.client:
            self._load_remote_files()
    
    def _create_toolbar(self) -> Gtk.Toolbar:
        """Create toolbar.
        
        Returns:
            Toolbar widget
        """
        toolbar = Gtk.Toolbar()
        
        # Authenticate button
        auth_button = Gtk.ToolButton()
        auth_button.set_label("Authenticate")
        auth_button.set_icon_name("dialog-password")
        auth_button.connect("clicked", self._on_auth_clicked)
        toolbar.insert(auth_button, 0)
        
        # Settings button
        settings_button = Gtk.ToolButton()
        settings_button.set_label("Settings")
        settings_button.set_icon_name("preferences-system")
        settings_button.connect("clicked", self._on_settings_clicked)
        toolbar.insert(settings_button, 1)
        
        return toolbar
    
    def _on_auth_clicked(self, widget) -> None:
        """Handle authentication button click."""
        dialog = AuthDialog(self)
        response = dialog.run()
        
        if response == Gtk.ResponseType.OK:
            client_id = dialog.client_id_entry.get_text()
            if client_id:
                self.config.set('client_id', client_id)
                self._authenticate()
        
        dialog.destroy()
    
    def _authenticate(self) -> None:
        """Perform OneDrive authentication."""
        client_id = self.config.client_id
        if not client_id:
            self._show_error("Please set Client ID in authentication dialog")
            return
        
        # Create temporary client for auth
        temp_client = OneDriveClient(client_id)
        auth_url = temp_client.get_auth_url()
        
        # Open browser for auth
        webbrowser.open(auth_url)
        
        # Start local server to receive callback
        def wait_for_callback():
            try:
                with socketserver.TCPServer(("", 8080), AuthCallbackHandler) as httpd:
                    httpd.handle_request()
                    
                    if AuthCallbackHandler.auth_code:
                        try:
                            token_data = temp_client.exchange_code(AuthCallbackHandler.auth_code)
                            self.config.save_token(token_data)
                            self.client = temp_client
                            
                            GLib.idle_add(self._on_auth_success)
                        except Exception as e:
                            logger.error(f"Auth failed: {e}")
                            GLib.idle_add(self._show_error, f"Authentication failed: {e}")
            except OSError as e:
                if e.errno == 98:  # Address already in use
                    GLib.idle_add(self._show_error, "Port 8080 is already in use. Please close other applications using this port.")
                else:
                    GLib.idle_add(self._show_error, f"Network error: {e}")
        
        thread = threading.Thread(target=wait_for_callback, daemon=True)
        thread.start()
        
        self._update_status("Waiting for authentication...")
    
    def _on_auth_success(self) -> None:
        """Handle successful authentication."""
        self._update_status("Authentication successful!")
        self._load_remote_files()
    
    def _on_settings_clicked(self, widget) -> None:
        """Handle settings button click."""
        dialog = SettingsDialog(self, self.config)
        dialog.run()
        dialog.destroy()
    
    def _on_tree_button_press(self, widget, event) -> bool:
        """Handle tree view button press.
        
        Returns:
            True if event handled
        """
        if event.type == Gdk.EventType.BUTTON_PRESS:
            selection = self.file_tree.get_selection()
            self.download_button.set_sensitive(selection.count_selected_rows() > 0)
        return False
    
    def _on_download_clicked(self, widget) -> None:
        """Handle download button click."""
        selection = self.file_tree.get_selection()
        model, paths = selection.get_selected_rows()
        
        if not paths:
            return
        
        for path in paths:
            iter = model.get_iter(path)
            file_name = model.get_value(iter, 0)
            file_id = model.get_value(iter, 4)
            is_local = model.get_value(iter, 3)
            
            if not is_local and file_id:
                self._download_file(file_id, file_name)
    
    def _on_refresh_clicked(self, widget) -> None:
        """Handle refresh button click."""
        self._load_remote_files()
    
    def _load_remote_files(self) -> None:
        """Load files from OneDrive."""
        if not self.client:
            self._show_error("Not authenticated. Please authenticate first.")
            return
        
        self._update_status("Loading files from OneDrive...")
        
        def load_in_thread():
            try:
                files = self.client.list_all_files()
                GLib.idle_add(self._update_file_list, files)
            except Exception as e:
                logger.error(f"Failed to load files: {e}")
                GLib.idle_add(self._show_error, f"Failed to load files: {e}")
        
        thread = threading.Thread(target=load_in_thread, daemon=True)
        thread.start()
    
    def _update_file_list(self, files: List[Dict[str, Any]]) -> None:
        """Update file list view.
        
        Args:
            files: List of file metadata
        """
        self.remote_files = files
        self.file_store.clear()
        
        sync_dir = self.config.sync_directory
        
        for file in files:
            name = file.get('name', 'Unknown')
            size = self._format_size(file.get('size', 0))
            modified = file.get('lastModifiedDateTime', '')
            file_id = file.get('id', '')
            
            # Check if file exists locally
            # Extract path from parentReference
            parent_path = file.get('parentReference', {}).get('path', '')
            if parent_path:
                parent_path = parent_path.replace('/drive/root:', '')
            
            local_path = sync_dir / parent_path.lstrip('/') / name
            is_local = local_path.exists()
            
            self.file_store.append([name, size, modified, is_local, file_id])
        
        self._update_status(f"Loaded {len(files)} files")
    
    def _download_file(self, file_id: str, file_name: str) -> None:
        """Download file from OneDrive.
        
        Args:
            file_id: OneDrive file ID
            file_name: File name
        """
        self._update_status(f"Downloading {file_name}...")
        
        def download_in_thread():
            try:
                # Get full file info to determine path
                file_info = self.client.get_file_metadata(file_id)
                parent_path = file_info.get('parentReference', {}).get('path', '')
                if parent_path:
                    parent_path = parent_path.replace('/drive/root:', '')
                
                local_path = self.config.sync_directory / parent_path.lstrip('/') / file_name
                self.client.download_file(file_id, local_path)
                
                GLib.idle_add(self._update_status, f"Downloaded {file_name}")
                GLib.idle_add(self._load_remote_files)  # Refresh to update local status
            except Exception as e:
                logger.error(f"Failed to download {file_name}: {e}")
                GLib.idle_add(self._show_error, f"Failed to download: {e}")
        
        thread = threading.Thread(target=download_in_thread, daemon=True)
        thread.start()
    
    def _format_size(self, size: int) -> str:
        """Format file size.
        
        Args:
            size: Size in bytes
            
        Returns:
            Formatted size string
        """
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"
    
    def _update_status(self, message: str) -> None:
        """Update status label.
        
        Args:
            message: Status message
        """
        self.status_label.set_markup(f"<i>Status: {message}</i>")
    
    def _show_error(self, message: str) -> None:
        """Show error dialog.
        
        Args:
            message: Error message
        """
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Error",
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()


class AuthDialog(Gtk.Dialog):
    """Authentication configuration dialog."""
    
    def __init__(self, parent):
        """Initialize dialog."""
        Gtk.Dialog.__init__(self, title="OneDrive Authentication", transient_for=parent, flags=0)
        self.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "OK", Gtk.ResponseType.OK
        )
        
        self.set_default_size(400, 150)
        
        box = self.get_content_area()
        
        label = Gtk.Label(label="Enter your Microsoft Application Client ID:")
        box.add(label)
        
        self.client_id_entry = Gtk.Entry()
        box.add(self.client_id_entry)
        
        info_label = Gtk.Label()
        info_label.set_markup(
            '<small>Get a Client ID at: '
            '<a href="https://portal.azure.com">Azure Portal</a></small>'
        )
        box.add(info_label)
        
        self.show_all()


class SettingsDialog(Gtk.Dialog):
    """Settings dialog."""
    
    def __init__(self, parent, config: Config):
        """Initialize dialog."""
        Gtk.Dialog.__init__(self, title="Settings", transient_for=parent, flags=0)
        self.add_buttons("Close", Gtk.ResponseType.CLOSE)
        
        self.config = config
        self.set_default_size(400, 200)
        
        box = self.get_content_area()
        box.set_spacing(10)
        
        # Sync directory
        hbox1 = Gtk.Box(spacing=6)
        label1 = Gtk.Label(label="Sync Directory:")
        label1.set_width_chars(20)
        label1.set_halign(Gtk.Align.START)
        hbox1.pack_start(label1, False, False, 0)
        
        self.sync_dir_button = Gtk.FileChooserButton(title="Select Sync Directory")
        self.sync_dir_button.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
        self.sync_dir_button.set_filename(str(config.sync_directory))
        self.sync_dir_button.connect("file-set", self._on_sync_dir_changed)
        hbox1.pack_start(self.sync_dir_button, True, True, 0)
        
        box.add(hbox1)
        
        # Sync interval
        hbox2 = Gtk.Box(spacing=6)
        label2 = Gtk.Label(label="Sync Interval (sec):")
        label2.set_width_chars(20)
        label2.set_halign(Gtk.Align.START)
        hbox2.pack_start(label2, False, False, 0)
        
        adjustment = Gtk.Adjustment(value=config.sync_interval, lower=60, upper=3600, step_increment=60)
        self.interval_spin = Gtk.SpinButton(adjustment=adjustment)
        self.interval_spin.connect("value-changed", self._on_interval_changed)
        hbox2.pack_start(self.interval_spin, False, False, 0)
        
        box.add(hbox2)
        
        self.show_all()
    
    def _on_sync_dir_changed(self, widget) -> None:
        """Handle sync directory change."""
        path = widget.get_filename()
        if path:
            self.config.sync_directory = Path(path)
    
    def _on_interval_changed(self, widget) -> None:
        """Handle sync interval change."""
        value = int(widget.get_value())
        self.config.set('sync_interval', value)


def main():
    """Main entry point for GUI."""
    win = OneDriveGUI()
    win.show_all()
    Gtk.main()


if __name__ == '__main__':
    main()
