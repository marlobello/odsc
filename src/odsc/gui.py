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
from .logging_config import setup_logging

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
        
        # Setup logging
        setup_logging(level=self.config.log_level, log_file=self.config.log_path)
        logger.info("=== ODSC GUI Starting ===")
        logger.info(f"Config directory: {self.config.config_dir}")
        logger.info(f"Log level: {self.config.log_level}")
        
        self.client: Optional[OneDriveClient] = None
        self.remote_files: List[Dict[str, Any]] = []
        
        # Initialize client if authenticated
        if self.config.load_token():
            logger.info("Found existing token, initializing client")
            self._init_client()
        else:
            logger.info("No existing token found")
        
        self._build_ui()
        self.connect("destroy", Gtk.main_quit)
    
    def _init_client(self) -> bool:
        """Initialize OneDrive client.
        
        Returns:
            True if successful
        """
        # client_id is optional - will use default if not configured
        client_id = self.config.client_id or None
        
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
        
        # TreeView for file/folder hierarchy (icon, name, size, modified, local, id, is_folder, path)
        self.file_store = Gtk.TreeStore(str, str, str, str, bool, str, bool, str)
        self.file_tree = Gtk.TreeView(model=self.file_store)
        self.file_tree.set_enable_tree_lines(True)
        
        # Column 1: Icon + Name
        column_name = Gtk.TreeViewColumn("Name")
        
        # Icon renderer
        renderer_icon = Gtk.CellRendererPixbuf()
        column_name.pack_start(renderer_icon, False)
        column_name.add_attribute(renderer_icon, "icon-name", 0)
        
        # Text renderer
        renderer_text = Gtk.CellRendererText()
        column_name.pack_start(renderer_text, True)
        column_name.add_attribute(renderer_text, "text", 1)
        column_name.set_resizable(True)
        column_name.set_min_width(300)
        self.file_tree.append_column(column_name)
        
        # Column 2: Size
        column_size = Gtk.TreeViewColumn("Size", renderer_text, text=2)
        column_size.set_resizable(True)
        self.file_tree.append_column(column_size)
        
        # Column 3: Modified
        column_modified = Gtk.TreeViewColumn("Modified", renderer_text, text=3)
        column_modified.set_resizable(True)
        self.file_tree.append_column(column_modified)
        
        # Column 4: Local copy status (cloud icon)
        column_local = Gtk.TreeViewColumn("Local Copy")
        renderer_cloud = Gtk.CellRendererPixbuf()
        column_local.pack_start(renderer_cloud, False)
        column_local.set_cell_data_func(renderer_cloud, self._render_cloud_icon)
        self.file_tree.append_column(column_local)
        
        scrolled.add(self.file_tree)
        
        # Context menu for downloading
        self.file_tree.connect("button-press-event", self._on_tree_button_press)
        
        # Bottom button bar
        button_box = Gtk.Box(spacing=6)
        vbox.pack_start(button_box, False, False, 0)
        
        self.keep_local_button = Gtk.Button(label="Keep Local Copy")
        self.keep_local_button.connect("clicked", self._on_keep_local_clicked)
        self.keep_local_button.set_sensitive(False)
        button_box.pack_start(self.keep_local_button, False, False, 0)
        
        self.remove_local_button = Gtk.Button(label="Remove Local Copy")
        self.remove_local_button.connect("clicked", self._on_remove_local_clicked)
        self.remove_local_button.set_sensitive(False)
        button_box.pack_start(self.remove_local_button, False, False, 0)
        
        self.refresh_button = Gtk.Button(label="Refresh")
        self.refresh_button.connect("clicked", self._on_refresh_clicked)
        button_box.pack_start(self.refresh_button, False, False, 0)
        
        # Load files if authenticated
        if self.client:
            self._load_remote_files()
    
    def _render_cloud_icon(self, column, cell, model, iter, data):
        """Render cloud icon based on local copy status.
        
        Args:
            column: TreeViewColumn
            cell: CellRenderer
            model: TreeModel
            iter: TreeIter
            data: User data
        """
        is_local = model.get_value(iter, 4)  # Column 4 is local status
        is_folder = model.get_value(iter, 6)  # Column 6 is folder flag
        
        if is_folder:
            # Don't show cloud icon for folders
            cell.set_property('icon-name', None)
        elif is_local:
            # Filled cloud icon for local copies
            cell.set_property('icon-name', 'folder-download')
        else:
            # Outlined cloud icon for remote-only
            cell.set_property('icon-name', 'cloud-download-symbolic')
    
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
        # Show authentication info dialog
        dialog = AuthInfoDialog(self, self.config, self.client)
        response = dialog.run()
        
        if response == Gtk.ResponseType.OK:
            # User wants to authenticate
            self._authenticate()
        elif response == 1:  # Custom response for logout
            # User wants to log out
            self._logout()
        
        dialog.destroy()
    
    def _authenticate(self) -> None:
        """Perform OneDrive authentication."""
        # Use configured client_id or None to use default
        client_id = self.config.client_id or None
        
        logger.info("=== Starting Authentication Flow ===")
        logger.info(f"Using client_id: {client_id if client_id else 'DEFAULT'}")
        
        # Create temporary client for auth
        temp_client = OneDriveClient(client_id)
        auth_url = temp_client.get_auth_url()
        
        logger.info(f"Opening browser for authentication")
        # Open browser for auth
        webbrowser.open(auth_url)
        
        # Start local server to receive callback
        def wait_for_callback():
            try:
                logger.info("Starting local callback server on port 8080")
                with socketserver.TCPServer(("", 8080), AuthCallbackHandler) as httpd:
                    logger.debug("Waiting for OAuth callback...")
                    httpd.handle_request()
                    
                    if AuthCallbackHandler.auth_code:
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
                if e.errno == 98:  # Address already in use
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
        self._load_remote_files()
    
    def _logout(self) -> None:
        """Log out and clear authentication."""
        # Remove token file
        if self.config.token_path.exists():
            self.config.token_path.unlink()
        
        # Clear client
        self.client = None
        
        # Clear file list
        self.file_store.clear()
        
        # Update UI
        self.download_button.set_sensitive(False)
        self._update_status("Logged out successfully")
        
        # Show info dialog
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Logged Out",
        )
        dialog.format_secondary_text("You have been logged out successfully.")
        dialog.run()
        dialog.destroy()
    
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
            model, paths = selection.get_selected_rows()
            
            if paths:
                # Check what's selected to enable appropriate buttons
                has_remote_only = False
                has_local_copy = False
                
                for path in paths:
                    iter = model.get_iter(path)
                    is_local = model.get_value(iter, 4)
                    is_folder = model.get_value(iter, 6)
                    file_id = model.get_value(iter, 5)
                    
                    # Skip folders
                    if is_folder:
                        continue
                    
                    if is_local:
                        has_local_copy = True
                    elif file_id:  # Has file_id means it's on OneDrive
                        has_remote_only = True
                
                self.keep_local_button.set_sensitive(has_remote_only)
                self.remove_local_button.set_sensitive(has_local_copy)
            else:
                self.keep_local_button.set_sensitive(False)
                self.remove_local_button.set_sensitive(False)
        
        return False
    
    def _on_keep_local_clicked(self, widget) -> None:
        """Handle keep local copy button click."""
        selection = self.file_tree.get_selection()
        model, paths = selection.get_selected_rows()
        
        if not paths:
            return
        
        for path in paths:
            iter = model.get_iter(path)
            file_name = model.get_value(iter, 1)
            file_id = model.get_value(iter, 5)
            is_local = model.get_value(iter, 4)
            is_folder = model.get_value(iter, 6)
            
            # Skip folders and files that are already local
            if is_folder or is_local:
                continue
            
            if file_id:
                self._download_file(file_id, file_name)
    
    def _on_remove_local_clicked(self, widget) -> None:
        """Handle remove local copy button click."""
        selection = self.file_tree.get_selection()
        model, paths = selection.get_selected_rows()
        
        if not paths:
            return
        
        # Confirm deletion
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Remove Local Copy?",
        )
        dialog.format_secondary_text(
            f"Remove local copy of {len(paths)} selected file(s)?\n\n"
            "Files will remain on OneDrive and can be downloaded again later."
        )
        response = dialog.run()
        dialog.destroy()
        
        if response != Gtk.ResponseType.YES:
            return
        
        for path in paths:
            iter = model.get_iter(path)
            file_name = model.get_value(iter, 1)
            file_path_str = model.get_value(iter, 7)  # Full path
            is_local = model.get_value(iter, 4)
            is_folder = model.get_value(iter, 6)
            
            # Skip folders and files that aren't local
            if is_folder or not is_local:
                continue
            
            self._remove_local_file(file_path_str, file_name)
    
    def _remove_local_file(self, rel_path: str, file_name: str) -> None:
        """Remove local copy of a file.
        
        Args:
            rel_path: Relative path to file
            file_name: File name for display
        """
        self._update_status(f"Removing local copy of {file_name}...")
        
        def remove_in_thread():
            try:
                local_path = self.config.sync_directory / rel_path
                
                if local_path.exists():
                    local_path.unlink()
                    logger.info(f"Removed local copy: {rel_path}")
                    
                    # Update sync state to mark as not downloaded
                    state = self.config.load_state()
                    if 'files' in state and rel_path in state['files']:
                        state['files'][rel_path]['downloaded'] = False
                        self.config.save_state(state)
                    
                    GLib.idle_add(self._update_status, f"Removed local copy of {file_name}")
                    GLib.idle_add(self._load_remote_files)  # Refresh
                else:
                    logger.warning(f"File not found locally: {rel_path}")
                    GLib.idle_add(self._update_status, "File not found locally")
                    
            except Exception as e:
                logger.error(f"Failed to remove local copy of {file_name}: {e}")
                GLib.idle_add(self._show_error, f"Failed to remove: {e}")
        
        thread = threading.Thread(target=remove_in_thread, daemon=True)
        thread.start()
    
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
        """Update file list view with folder hierarchy.
        
        Args:
            files: List of file metadata from OneDrive
        """
        self.remote_files = files
        self.file_store.clear()
        
        sync_dir = self.config.sync_directory
        
        logger.debug(f"Building file tree with {len(files)} items")
        
        # Build folder hierarchy
        # Key: folder path, Value: TreeIter
        folder_iters = {}
        
        # Track which files exist on OneDrive
        remote_files_set = set()
        
        # Sort: folders first, then by path
        sorted_items = sorted(files, key=lambda x: (
            'folder' not in x,  # Folders first
            x.get('parentReference', {}).get('path', ''),
            x.get('name', '')
        ))
        
        for item in sorted_items:
            name = item.get('name', 'Unknown')
            is_folder = 'folder' in item
            item_id = item.get('id', '')
            
            # Get parent path
            parent_ref = item.get('parentReference', {})
            parent_path = parent_ref.get('path', '')
            if parent_path:
                parent_path = parent_path.replace('/drive/root:', '')
            
            # Build full path
            if parent_path:
                full_path = parent_path.lstrip('/') + '/' + name
            else:
                full_path = name
            
            remote_files_set.add(full_path)
            
            # Determine parent iter
            parent_iter = None
            if parent_path and parent_path != '/':
                parent_iter = folder_iters.get(parent_path.lstrip('/'))
            
            if is_folder:
                # Add folder
                icon = "folder"
                size_str = ""
                modified = ""
                is_local = (sync_dir / full_path).exists()
                
                iter = self.file_store.append(parent_iter, [
                    icon, name, size_str, modified, is_local, item_id, True, full_path
                ])
                folder_iters[full_path] = iter
                logger.debug(f"Added folder: {full_path}")
                
            else:
                # Add file
                icon = self._get_file_icon(name)
                size = self._format_size(item.get('size', 0))
                modified = item.get('lastModifiedDateTime', '')[:10] if 'lastModifiedDateTime' in item else ''
                
                # Check if file exists locally
                local_path = sync_dir / full_path
                is_local = local_path.exists()
                
                self.file_store.append(parent_iter, [
                    icon, name, size, modified, is_local, item_id, False, full_path
                ])
                logger.debug(f"Added file: {full_path}")
        
        # Add local files that aren't on OneDrive yet (pending upload)
        logger.debug("Scanning for local files pending upload...")
        self._add_pending_uploads(sync_dir, remote_files_set, folder_iters)
        
        # Expand root level folders
        self.file_tree.expand_row(Gtk.TreePath.new_first(), False)
        
        self._update_status(f"Loaded {len(files)} items")
    
    def _add_pending_uploads(self, sync_dir: Path, remote_files_set: set, folder_iters: Dict) -> None:
        """Add local files that haven't been uploaded to OneDrive yet.
        
        Args:
            sync_dir: Local sync directory
            remote_files_set: Set of paths that exist on OneDrive
            folder_iters: Dictionary of folder path to TreeIter
        """
        pending_count = 0
        
        for path in sync_dir.rglob('*'):
            # Skip hidden files and OneDrive config
            if any(part.startswith('.') for part in path.parts):
                continue
            
            if path.is_file():
                try:
                    rel_path = str(path.relative_to(sync_dir))
                    
                    # Check if this file is on OneDrive
                    if rel_path not in remote_files_set:
                        # This is a local file pending upload
                        name = path.name
                        parent_path = str(path.parent.relative_to(sync_dir)) if path.parent != sync_dir else ""
                        
                        # Get parent iter
                        parent_iter = None
                        if parent_path and parent_path != '.':
                            parent_iter = folder_iters.get(parent_path)
                            
                            # If parent folder doesn't exist in tree, skip for now
                            if parent_iter is None and parent_path:
                                logger.debug(f"Parent folder not in tree, skipping: {rel_path}")
                                continue
                        
                        # Add file with upload icon
                        icon = "emblem-synchronizing"  # Upload pending icon
                        size = self._format_size(path.stat().st_size)
                        modified = ""
                        
                        self.file_store.append(parent_iter, [
                            icon, f"{name} (pending upload)", size, modified, True, "", False, rel_path
                        ])
                        pending_count += 1
                        logger.debug(f"Added pending upload: {rel_path}")
                        
                except (OSError, ValueError) as e:
                    logger.warning(f"Cannot process {path}: {e}")
        
        if pending_count > 0:
            logger.info(f"Found {pending_count} files pending upload")
    
    def _get_file_icon(self, filename: str) -> str:
        """Get icon name for file type.
        
        Args:
            filename: File name
            
        Returns:
            GTK icon name
        """
        # Get file extension
        ext = filename.lower().split('.')[-1] if '.' in filename else ''
        
        # Map extensions to GTK icon names
        icon_map = {
            # Documents
            'pdf': 'x-office-document',
            'doc': 'x-office-document',
            'docx': 'x-office-document',
            'odt': 'x-office-document',
            'txt': 'text-x-generic',
            'rtf': 'text-x-generic',
            
            # Spreadsheets
            'xls': 'x-office-spreadsheet',
            'xlsx': 'x-office-spreadsheet',
            'ods': 'x-office-spreadsheet',
            'csv': 'x-office-spreadsheet',
            
            # Presentations
            'ppt': 'x-office-presentation',
            'pptx': 'x-office-presentation',
            'odp': 'x-office-presentation',
            
            # Images
            'jpg': 'image-x-generic',
            'jpeg': 'image-x-generic',
            'png': 'image-x-generic',
            'gif': 'image-x-generic',
            'bmp': 'image-x-generic',
            'svg': 'image-x-generic',
            'ico': 'image-x-generic',
            
            # Video
            'mp4': 'video-x-generic',
            'avi': 'video-x-generic',
            'mkv': 'video-x-generic',
            'mov': 'video-x-generic',
            'wmv': 'video-x-generic',
            'flv': 'video-x-generic',
            
            # Audio
            'mp3': 'audio-x-generic',
            'wav': 'audio-x-generic',
            'flac': 'audio-x-generic',
            'ogg': 'audio-x-generic',
            'm4a': 'audio-x-generic',
            
            # Archives
            'zip': 'package-x-generic',
            'rar': 'package-x-generic',
            'tar': 'package-x-generic',
            'gz': 'package-x-generic',
            '7z': 'package-x-generic',
            
            # Code
            'py': 'text-x-script',
            'js': 'text-x-script',
            'java': 'text-x-script',
            'c': 'text-x-script',
            'cpp': 'text-x-script',
            'h': 'text-x-script',
            'sh': 'text-x-script',
            'html': 'text-html',
            'css': 'text-x-script',
            'xml': 'text-html',
            'json': 'text-x-script',
        }
        
        return icon_map.get(ext, 'text-x-generic')
    
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
                
                rel_path = (parent_path.lstrip('/') + '/' + file_name) if parent_path else file_name
                local_path = self.config.sync_directory / rel_path
                
                # Download file
                metadata = self.client.download_file(file_id, local_path)
                
                # Update sync state to mark as downloaded
                state = self.config.load_state()
                if 'files' not in state:
                    state['files'] = {}
                
                state['files'][rel_path] = {
                    'mtime': local_path.stat().st_mtime,
                    'size': file_info.get('size', 0),
                    'eTag': metadata.get('eTag', ''),
                    'remote_modified': metadata.get('lastModifiedDateTime', ''),
                    'downloaded': True,  # Mark as explicitly downloaded by user
                }
                self.config.save_state(state)
                
                logger.info(f"Downloaded and marked for sync: {rel_path}")
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


class AuthInfoDialog(Gtk.Dialog):
    """Authentication information and management dialog."""
    
    def __init__(self, parent, config: Config, client: Optional[OneDriveClient]):
        """Initialize dialog.
        
        Args:
            parent: Parent window
            config: Configuration object
            client: OneDrive client (None if not authenticated)
        """
        Gtk.Dialog.__init__(self, title="Authentication", transient_for=parent, flags=0)
        
        self.config = config
        self.client = client
        self.set_default_size(500, 300)
        self.set_border_width(10)
        
        box = self.get_content_area()
        box.set_spacing(10)
        
        # Check if authenticated
        token_data = config.load_token()
        is_authenticated = token_data is not None and client is not None
        
        if is_authenticated:
            # Show authentication info
            title_label = Gtk.Label()
            title_label.set_markup("<b>Authentication Status</b>")
            title_label.set_halign(Gtk.Align.START)
            box.add(title_label)
            
            # Status
            status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            status_label = Gtk.Label(label="Status:")
            status_label.set_width_chars(15)
            status_label.set_halign(Gtk.Align.START)
            status_value = Gtk.Label(label="✓ Authenticated")
            status_value.set_halign(Gtk.Align.START)
            status_box.pack_start(status_label, False, False, 0)
            status_box.pack_start(status_value, False, False, 0)
            box.add(status_box)
            
            # Client ID
            client_id_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            client_id_label = Gtk.Label(label="Client ID:")
            client_id_label.set_width_chars(15)
            client_id_label.set_halign(Gtk.Align.START)
            client_id_value = Gtk.Label(label=client.client_id if client else "Unknown")
            client_id_value.set_halign(Gtk.Align.START)
            client_id_value.set_selectable(True)
            client_id_box.pack_start(client_id_label, False, False, 0)
            client_id_box.pack_start(client_id_value, False, False, 0)
            box.add(client_id_box)
            
            # Token expiry info
            if token_data and 'expires_at' in token_data:
                import time
                from datetime import datetime
                expires_at = token_data['expires_at']
                expires_datetime = datetime.fromtimestamp(expires_at)
                time_remaining = expires_at - time.time()
                
                expiry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                expiry_label = Gtk.Label(label="Token Expires:")
                expiry_label.set_width_chars(15)
                expiry_label.set_halign(Gtk.Align.START)
                
                if time_remaining > 0:
                    hours = int(time_remaining / 3600)
                    expiry_text = f"{expires_datetime.strftime('%Y-%m-%d %H:%M')} ({hours}h remaining)"
                else:
                    expiry_text = "Expired (will auto-refresh)"
                
                expiry_value = Gtk.Label(label=expiry_text)
                expiry_value.set_halign(Gtk.Align.START)
                expiry_box.pack_start(expiry_label, False, False, 0)
                expiry_box.pack_start(expiry_value, False, False, 0)
                box.add(expiry_box)
            
            # Has refresh token?
            if token_data and 'refresh_token' in token_data:
                refresh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                refresh_label = Gtk.Label(label="Refresh Token:")
                refresh_label.set_width_chars(15)
                refresh_label.set_halign(Gtk.Align.START)
                refresh_value = Gtk.Label(label="✓ Available")
                refresh_value.set_halign(Gtk.Align.START)
                refresh_box.pack_start(refresh_label, False, False, 0)
                refresh_box.pack_start(refresh_value, False, False, 0)
                box.add(refresh_box)
            
            # Token file location
            token_file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            token_file_label = Gtk.Label(label="Token File:")
            token_file_label.set_width_chars(15)
            token_file_label.set_halign(Gtk.Align.START)
            token_file_value = Gtk.Label(label=str(config.token_path))
            token_file_value.set_halign(Gtk.Align.START)
            token_file_value.set_selectable(True)
            token_file_value.set_line_wrap(True)
            token_file_box.pack_start(token_file_label, False, False, 0)
            token_file_box.pack_start(token_file_value, False, False, 0)
            box.add(token_file_box)
            
            # Buttons
            self.add_button("Log Out", 1)  # Custom response ID
            self.add_button("Re-authenticate", Gtk.ResponseType.OK)
            self.add_button("Close", Gtk.ResponseType.CANCEL)
            
        else:
            # Not authenticated
            title_label = Gtk.Label()
            title_label.set_markup("<b>Not Authenticated</b>")
            title_label.set_halign(Gtk.Align.START)
            box.add(title_label)
            
            info_label = Gtk.Label(
                label="You are not currently authenticated with OneDrive.\n\n"
                      "Click 'Authenticate' to log in with your Microsoft account."
            )
            info_label.set_halign(Gtk.Align.START)
            info_label.set_line_wrap(True)
            box.add(info_label)
            
            # Show client ID that will be used
            client_id = config.client_id or OneDriveClient.DEFAULT_CLIENT_ID
            client_id_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            client_id_label = Gtk.Label(label="Client ID:")
            client_id_label.set_width_chars(15)
            client_id_label.set_halign(Gtk.Align.START)
            client_id_value = Gtk.Label(label=client_id)
            client_id_value.set_halign(Gtk.Align.START)
            client_id_value.set_selectable(True)
            client_id_box.pack_start(client_id_label, False, False, 0)
            client_id_box.pack_start(client_id_value, False, False, 0)
            box.add(client_id_box)
            
            # Buttons
            self.add_button("Authenticate", Gtk.ResponseType.OK)
            self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        
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
