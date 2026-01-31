"""Main window for ODSC GUI."""

import html
import logging
import subprocess
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from ..config import Config
from ..onedrive_client import OneDriveClient
from ..logging_config import setup_logging
from ..path_utils import sanitize_onedrive_path, validate_sync_path, SecurityError
from .dialogs import DialogHelper
from .menu_bar import MenuBarMixin
from .file_tree_view import FileTreeViewMixin
from .file_operations import FileOperationsMixin

logger = logging.getLogger(__name__)


class OneDriveGUI(MenuBarMixin, FileTreeViewMixin, FileOperationsMixin, Gtk.ApplicationWindow):
    """Main GNOME GUI window for OneDrive Sync Client."""
    
    def __init__(self, application):
        """Initialize GUI."""
        Gtk.ApplicationWindow.__init__(self, application=application, title="OneDrive Sync Client")
        
        self.set_default_size(800, 600)
        self.set_border_width(10)
        
        self.set_icon_name("odsc")
        Gtk.Window.set_default_icon_name("odsc")
        
        self.config = Config()
        
        setup_logging(level=self.config.log_level, log_file=self.config.log_path)
        logger.info("=== ODSC GUI Starting ===")
        logger.info(f"Config directory: {self.config.config_dir}")
        logger.info(f"Log level: {self.config.log_level}")
        
        self.client: Optional[OneDriveClient] = None
        self.remote_files: List[Dict[str, Any]] = []
        
        self.executor = ThreadPoolExecutor(max_workers=2)
        
        self.login_menu_item: Optional[Gtk.MenuItem] = None
        self.logout_menu_item: Optional[Gtk.MenuItem] = None
        
        self.log_panel_visible = False
        self.log_text_view: Optional[Gtk.TextView] = None
        self.log_panel: Optional[Gtk.Box] = None
        self.main_paned: Optional[Gtk.Paned] = None
        self.log_file_position = 0
        self.log_tail_timer_id = None
        
        self._init_tree_view_cache()
        
        if self.config.load_token():
            logger.info("Found existing token, initializing client")
            self._init_client()
        else:
            logger.info("No existing token found")
        
        self._build_ui()
        
        self._update_auth_menu_state()
    
    def _init_client(self) -> bool:
        """Initialize OneDrive client.
        
        Returns:
            True if successful
        """
        client_id = self.config.client_id or None
        
        token_data = self.config.load_token()
        self.client = OneDriveClient(client_id, token_data)
        return True
    
    def _build_ui(self) -> None:
        """Build the user interface."""
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)
        
        menubar = self._create_menubar()
        vbox.pack_start(menubar, False, False, 0)
        
        self.main_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        vbox.pack_start(self.main_paned, True, True, 0)
        
        main_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.main_paned.pack1(main_content_box, resize=True, shrink=False)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        main_content_box.pack_start(scrolled, True, True, 0)
        
        self.file_store = Gtk.TreeStore(str, str, str, str, bool, str, bool, str, str)
        self.file_tree = Gtk.TreeView(model=self.file_store)
        self.file_tree.set_enable_tree_lines(True)
        
        self.file_tree.set_has_tooltip(True)
        self.file_tree.connect("query-tooltip", self._on_tree_query_tooltip)
        
        column_name = Gtk.TreeViewColumn("Name")
        
        renderer_icon = Gtk.CellRendererPixbuf()
        renderer_icon.set_padding(4, 2)
        column_name.pack_start(renderer_icon, False)
        column_name.add_attribute(renderer_icon, "icon-name", 0)
        
        renderer_name = Gtk.CellRendererText()
        renderer_name.set_padding(6, 4)
        column_name.pack_start(renderer_name, True)
        column_name.add_attribute(renderer_name, "text", 1)
        column_name.set_resizable(True)
        column_name.set_min_width(300)
        self.file_tree.append_column(column_name)
        
        renderer_size = Gtk.CellRendererText()
        renderer_size.set_padding(8, 4)
        renderer_size.set_alignment(1.0, 0.5)
        column_size = Gtk.TreeViewColumn("Size", renderer_size, text=2)
        column_size.set_alignment(1.0)
        column_size.set_resizable(True)
        self.file_tree.append_column(column_size)
        
        renderer_modified = Gtk.CellRendererText()
        renderer_modified.set_padding(8, 4)
        column_modified = Gtk.TreeViewColumn("Modified", renderer_modified, text=3)
        column_modified.set_resizable(True)
        self.file_tree.append_column(column_modified)
        
        column_status = Gtk.TreeViewColumn("Status")
        column_status.set_alignment(0.5)
        renderer_status = Gtk.CellRendererPixbuf()
        renderer_status.set_padding(8, 4)
        renderer_status.set_alignment(0.5, 0.5)
        column_status.pack_start(renderer_status, False)
        column_status.set_cell_data_func(renderer_status, self._render_status_icon)
        self.file_tree.append_column(column_status)
        
        scrolled.add(self.file_tree)
        
        selection = self.file_tree.get_selection()
        selection.connect("changed", self._on_selection_changed)
        
        button_box = Gtk.Box(spacing=6)
        main_content_box.pack_start(button_box, False, False, 0)
        
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
        
        self.status_label = Gtk.Label()
        self.status_label.set_markup("<i>Status: Ready</i>")
        self.status_label.set_halign(Gtk.Align.END)
        button_box.pack_end(self.status_label, False, False, 6)
        
        self._create_log_panel()
        
        self.service_info_bar = None
        
        if self.client:
            self._load_remote_files()
        
        GLib.timeout_add_seconds(2, self._check_service_status)
    
    def _create_log_panel(self) -> None:
        """Create the log panel (initially hidden)."""
        self.log_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.log_panel.set_border_width(6)
        
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.log_panel.pack_start(header_box, False, False, 0)
        
        log_label = Gtk.Label()
        # Escape path to prevent Pango markup injection
        log_label.set_markup(f"<b>Log: {html.escape(str(self.config.log_path))}</b>")
        log_label.set_halign(Gtk.Align.START)
        header_box.pack_start(log_label, True, True, 0)
        
        refresh_log_button = Gtk.Button(label="Refresh")
        refresh_log_button.connect("clicked", self._on_refresh_log_clicked)
        header_box.pack_start(refresh_log_button, False, False, 0)
        
        self.auto_scroll_check = Gtk.CheckButton(label="Auto-scroll")
        self.auto_scroll_check.set_active(True)
        header_box.pack_start(self.auto_scroll_check, False, False, 0)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(200)
        self.log_panel.pack_start(scrolled, True, True, 0)
        
        self.log_text_view = Gtk.TextView()
        self.log_text_view.set_editable(False)
        self.log_text_view.set_monospace(True)
        self.log_text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        scrolled.add(self.log_text_view)
    
    def _on_toggle_log_panel(self, widget) -> None:
        """Handle toggle log panel menu item."""
        if widget.get_active():
            self._show_log_panel()
        else:
            self._hide_log_panel()
    
    def _show_log_panel(self) -> None:
        """Show the log panel."""
        if not self.log_panel_visible:
            self.main_paned.pack2(self.log_panel, resize=False, shrink=False)
            self.log_panel.show_all()
            
            window_height = self.get_allocated_height()
            self.main_paned.set_position(int(window_height * 0.7))
            
            self._refresh_log_content()
            
            self.log_tail_timer_id = GLib.timeout_add(500, self._tail_log_file)
            
            self.log_panel_visible = True
            logger.info("Log panel shown, tailing started")
    
    def _hide_log_panel(self) -> None:
        """Hide the log panel."""
        if self.log_panel_visible:
            if self.log_tail_timer_id:
                GLib.source_remove(self.log_tail_timer_id)
                self.log_tail_timer_id = None
            
            self.main_paned.remove(self.log_panel)
            self.log_panel_visible = False
            logger.info("Log panel hidden, tailing stopped")
    
    def _on_refresh_log_clicked(self, widget) -> None:
        """Handle refresh log button click."""
        self._refresh_log_content()
    
    def _refresh_log_content(self) -> None:
        """Refresh the log content from file (full reload)."""
        log_path = self.config.log_path
        
        if not log_path.exists():
            buffer = self.log_text_view.get_buffer()
            buffer.set_text("Log file does not exist yet.\n")
            self.log_file_position = 0
            return
        
        try:
            with open(log_path, 'r') as f:
                log_content = f.read()
                self.log_file_position = f.tell()
            
            buffer = self.log_text_view.get_buffer()
            buffer.set_text(log_content)
            
            if self.auto_scroll_check.get_active():
                GLib.idle_add(self._scroll_log_to_end)
                
        except Exception as e:
            buffer = self.log_text_view.get_buffer()
            buffer.set_text(f"Error reading log file: {e}\n")
            self.log_file_position = 0
    
    def _tail_log_file(self) -> bool:
        """Tail the log file (read only new content).
        
        Returns:
            True to continue the timer, False to stop
        """
        log_path = self.config.log_path
        
        if not log_path.exists():
            return True
        
        try:
            with open(log_path, 'r') as f:
                f.seek(self.log_file_position)
                
                new_content = f.read()
                
                if new_content:
                    self.log_file_position = f.tell()
                    
                    buffer = self.log_text_view.get_buffer()
                    end_iter = buffer.get_end_iter()
                    buffer.insert(end_iter, new_content)
                    
                    if self.auto_scroll_check.get_active():
                        GLib.idle_add(self._scroll_log_to_end)
                        
        except Exception as e:
            logger.debug(f"Error tailing log file: {e}")
        
        return True
    
    def _scroll_log_to_end(self) -> bool:
        """Scroll log view to the end."""
        buffer = self.log_text_view.get_buffer()
        end_iter = buffer.get_end_iter()
        self.log_text_view.scroll_to_iter(end_iter, 0.0, False, 0.0, 0.0)
        return False
    
    def _restart_daemon(self) -> None:
        """Restart the ODSC daemon using systemctl."""
        logger.info("Attempting to restart daemon via systemctl")
        
        try:
            result = subprocess.run(
                ['systemctl', '--user', 'is-active', 'odsc'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            daemon_was_running = (result.returncode == 0)
            
            if daemon_was_running:
                result = subprocess.run(
                    ['systemctl', '--user', 'restart', 'odsc'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    logger.info("Daemon restarted successfully")
                    DialogHelper.show_info(
                        self,
                        "Daemon Restarted",
                        "The ODSC daemon has been restarted successfully.\n"
                        "Your new settings are now active."
                    )
                else:
                    logger.error(f"Failed to restart daemon: {result.stderr}")
                    self._show_error(f"Failed to restart daemon:\n{result.stderr}")
            else:
                logger.info("Daemon is not currently running")
                if DialogHelper.show_confirm(
                    self,
                    "Daemon Not Running",
                    "The ODSC daemon is not currently running.\n\n"
                    "Would you like to start it now?"
                ):
                    self._start_daemon()
                    
        except subprocess.TimeoutExpired:
            logger.error("Timeout while trying to restart daemon")
            self._show_error("Timeout while trying to restart daemon.\nPlease restart manually.")
        except FileNotFoundError:
            logger.error("systemctl command not found")
            self._show_error(
                "systemctl command not found.\n\n"
                "Please restart the daemon manually:\n"
                "  systemctl --user restart odsc"
            )
        except Exception as e:
            logger.error(f"Error restarting daemon: {e}", exc_info=True)
            self._show_error(f"Error restarting daemon:\n{e}")
    
    def _start_daemon(self) -> None:
        """Start the ODSC daemon using systemctl."""
        logger.info("Attempting to start daemon via systemctl")
        
        try:
            result = subprocess.run(
                ['systemctl', '--user', 'start', 'odsc'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                logger.info("Daemon started successfully")
                DialogHelper.show_info(
                    self,
                    "Daemon Started",
                    "The ODSC daemon has been started successfully.\n"
                    "Your settings are now active."
                )
            else:
                logger.error(f"Failed to start daemon: {result.stderr}")
                self._show_error(f"Failed to start daemon:\n{result.stderr}")
                
        except Exception as e:
            logger.error(f"Error starting daemon: {e}", exc_info=True)
            self._show_error(f"Error starting daemon:\n{e}")
    
    def _check_service_status(self) -> bool:
        """Check if the ODSC systemd service is running and notify user if not.
        
        Returns:
            False to stop the timer (one-time check)
        """
        try:
            result = subprocess.run(
                ['systemctl', '--user', 'is-active', 'odsc.service'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                logger.info("ODSC service is not running")
                self._show_service_not_running_bar()
            else:
                logger.info("ODSC service is running")
                
        except FileNotFoundError:
            logger.debug("systemctl not found, skipping service check")
        except Exception as e:
            logger.debug(f"Error checking service status: {e}")
        
        return False
    
    def _show_service_not_running_bar(self) -> None:
        """Show an info bar notifying that the service is not running."""
        if self.service_info_bar is not None:
            return
        
        self.service_info_bar = Gtk.InfoBar()
        self.service_info_bar.set_message_type(Gtk.MessageType.WARNING)
        
        content = self.service_info_bar.get_content_area()
        label = Gtk.Label()
        label.set_markup(
            "<b>OneDrive Sync Service Not Running</b>\n"
            "The background sync service is not running. "
            "Files will not automatically sync until the service is started."
        )
        label.set_line_wrap(True)
        label.set_halign(Gtk.Align.START)
        content.add(label)
        
        self.service_info_bar.add_button("Start Service", Gtk.ResponseType.ACCEPT)
        self.service_info_bar.add_button("Dismiss", Gtk.ResponseType.CLOSE)
        
        self.service_info_bar.connect("response", self._on_service_info_bar_response)
        
        vbox = self.get_children()[0]
        vbox.pack_start(self.service_info_bar, False, False, 0)
        vbox.reorder_child(self.service_info_bar, 1)
        
        self.service_info_bar.show_all()
        logger.info("Service not running notification shown")
    
    def _on_service_info_bar_response(self, info_bar, response_id) -> None:
        """Handle service info bar response.
        
        Args:
            info_bar: The InfoBar widget
            response_id: Response ID
        """
        if response_id == Gtk.ResponseType.ACCEPT:
            self._start_daemon_from_notification()
        
        self._hide_service_info_bar()
    
    def _start_daemon_from_notification(self) -> None:
        """Start the daemon from the notification bar."""
        try:
            result = subprocess.run(
                ['systemctl', '--user', 'start', 'odsc.service'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                logger.info("Service started successfully from notification")
                DialogHelper.show_info(
                    self,
                    "Service Started",
                    "The OneDrive Sync service has been started successfully.",
                    "Background synchronization is now active."
                )
            else:
                logger.error(f"Failed to start service: {result.stderr}")
                DialogHelper.show_error(
                    self,
                    "Failed to Start Service",
                    f"Could not start the OneDrive Sync service:\n\n{result.stderr}\n\n"
                    "You can start it manually with:\n  systemctl --user start odsc.service"
                )
        except Exception as e:
            logger.error(f"Error starting service: {e}", exc_info=True)
            DialogHelper.show_error(
                self,
                "Error Starting Service",
                f"An error occurred while starting the service:\n\n{str(e)}"
            )
    
    def _hide_service_info_bar(self) -> None:
        """Hide and destroy the service info bar."""
        if self.service_info_bar is not None:
            self.service_info_bar.destroy()
            self.service_info_bar = None
            logger.debug("Service info bar hidden")
    
    def _on_refresh_clicked(self, widget) -> None:
        """Handle refresh button click."""
        self._load_remote_files()
    
    def _load_remote_files(self) -> None:
        """Load files from OneDrive using delta query and caching."""
        if not self.client:
            self._show_error("Not authenticated. Please authenticate first.")
            return
        
        self._update_status("Loading files from OneDrive...")
        
        def load_in_thread():
            try:
                state = self.config.load_state()
                delta_token = state.get('delta_token')
                file_cache = state.get('file_cache', {})
                
                if delta_token and file_cache:
                    logger.info("Using delta query for incremental refresh")
                    GLib.idle_add(self._update_status, "Checking for changes...")
                    
                    changes, new_delta_token = self.client.get_delta(delta_token)
                    
                    for item in changes:
                        if item.get('deleted'):
                            item_id = item['id']
                            for path in list(file_cache.keys()):
                                if file_cache[path].get('id') == item_id:
                                    del file_cache[path]
                                    break
                        else:
                            try:
                                parent_path = item.get('parentReference', {}).get('path', '')
                                name = item.get('name', '')
                                
                                if parent_path:
                                    safe_parent = sanitize_onedrive_path(parent_path)
                                    full_path = str(Path(safe_parent) / name) if safe_parent else name
                                else:
                                    full_path = name
                                
                                file_cache[full_path] = item
                            except Exception as e:
                                logger.warning(f"Error processing change: {e}")
                    
                    state['delta_token'] = new_delta_token
                    state['file_cache'] = file_cache
                    self.config.save_state(state)
                    
                    files = []
                    for path, item in file_cache.items():
                        if 'name' not in item and path:
                            item = dict(item)
                            item['name'] = Path(path).name
                            item['_cache_path'] = path
                        files.append(item)
                    logger.info(f"Delta refresh complete: {len(changes)} changes, {len(files)} total items")
                else:
                    logger.info("Initial load: fetching all files")
                    GLib.idle_add(self._update_status, "Fetching all files (first time)...")
                    
                    changes, new_delta_token = self.client.get_delta(None)
                    
                    file_cache = {}
                    for item in changes:
                        if not item.get('deleted'):
                            try:
                                parent_path = item.get('parentReference', {}).get('path', '')
                                name = item.get('name', '')
                                
                                if parent_path:
                                    safe_parent = sanitize_onedrive_path(parent_path)
                                    full_path = str(Path(safe_parent) / name) if safe_parent else name
                                else:
                                    full_path = name
                                
                                file_cache[full_path] = item
                            except Exception as e:
                                logger.warning(f"Error processing item: {e}")
                    
                    state['delta_token'] = new_delta_token
                    state['file_cache'] = file_cache
                    self.config.save_state(state)
                    
                    files = changes
                    logger.info(f"Initial load complete: {len(files)} items")
                
                GLib.idle_add(self._update_file_list, files)
                GLib.idle_add(self._update_status, f"Loaded {len(files)} items")
                
            except Exception as e:
                logger.error(f"Failed to load files: {e}", exc_info=True)
                GLib.idle_add(self._show_error, "Failed to load files", str(e))
                GLib.idle_add(self._update_status, "Failed to load files")
        
        thread = threading.Thread(target=load_in_thread, daemon=True)
        thread.start()
    
    def _update_file_list(self, files: List[Dict[str, Any]]) -> None:
        """Update file list view with folder hierarchy using chunked rendering.
        
        Args:
            files: List of file metadata from OneDrive
        """
        self.remote_files = files
        
        expanded_paths = self._save_expanded_state()
        scroll_position = self._save_scroll_position()
        
        self.file_store.clear()
        self._clear_tree_view_cache()
        
        self._update_status(f"Processing {len(files)} items...")
        
        sync_dir = self.config.sync_directory
        
        logger.debug(f"Building file tree with {len(files)} items")
        
        folder_iters = {}
        remote_files_set = set()
        
        # Pre-compute sort keys for better performance
        items_with_keys = []
        for item in files:
            # Skip root folder artifact
            name = item.get('name', '')
            if name.lower() == 'root':
                parent_ref = item.get('parentReference', {})
                parent_path = parent_ref.get('path', '') if parent_ref else ''
                # Only skip if it's at the root level (no parent or parent is /drive/root)
                if not parent_path or parent_path in ('/drive/root', '/drive/root:', ''):
                    logger.debug(f"Skipping 'root' folder at root level")
                    continue
            
            is_folder = 'folder' in item or item.get('is_folder', False)
            if '_cache_path' in item:
                cache_path = item['_cache_path']
                parent = str(Path(cache_path).parent) if '/' in cache_path else ''
                sort_path = cache_path
            else:
                parent_ref = item.get('parentReference', {})
                parent = parent_ref.get('path', '') if parent_ref else ''
                sort_path = f"{parent}/{name}"
            
            sort_key = (not is_folder, parent, sort_path)
            items_with_keys.append((sort_key, item))
        
        # Sort once with pre-computed keys
        items_with_keys.sort(key=lambda x: x[0])
        sorted_items = [item for _, item in items_with_keys]
        
        # Use nonlocal variables accessible to nested function
        self._folder_iters = {}
        self._remote_files_set = set()
        
        # Load state ONCE before processing (not per file!)
        state = self.config.load_state()
        files_state = state.get('files', {})
        
        # Process items in chunks for responsive UI
        chunk_size = 50  # Process 50 items at a time for better responsiveness
        total_items = len(sorted_items)
        
        def process_chunk(start_idx):
            """Process a chunk of items and schedule next chunk."""
            end_idx = min(start_idx + chunk_size, total_items)
            
            for i in range(start_idx, end_idx):
                item = sorted_items[i]
                name = item.get('name', 'Unknown')
                is_folder = 'folder' in item or item.get('is_folder', False)
                item_id = item.get('id', '')
                
                try:
                    parent_ref = item.get('parentReference', {})
                    parent_path = parent_ref.get('path', '')
                    if parent_path:
                        parent_path = sanitize_onedrive_path(parent_path)
                    
                    if not parent_path and '_cache_path' in item:
                        cache_path = item['_cache_path']
                        if '/' in cache_path:
                            parent_path = str(Path(cache_path).parent)
                            if parent_path == '.':
                                parent_path = ''
                        full_path = cache_path
                    else:
                        if parent_path:
                            full_path = str(Path(parent_path) / name)
                        else:
                            full_path = name
                    
                    # Skip root folder artifact
                    if full_path.lower() in ('root', 'root/', '/root') or name.lower() == 'root':
                        logger.debug(f"Skipping 'root' folder artifact: {name}")
                        continue
                    
                    validated_path = validate_sync_path(full_path, sync_dir)
                    
                    self._remote_files_set.add(full_path)
                
                except SecurityError as e:
                    logger.warning(f"Skipping unsafe path for {name}: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"Error processing item {name}: {e}")
                    continue
                
                parent_iter = None
                if parent_path and parent_path != '/':
                    parent_iter = self._folder_iters.get(parent_path.lstrip('/'))
                    
                    # If parent folder doesn't exist yet, create it (and ancestors)
                    if parent_iter is None and parent_path:
                        parent_iter = self._ensure_parent_folders(parent_path, sync_dir)
                
                if is_folder:
                    # Skip if this folder was already added (deduplication)
                    if full_path in self._folder_iters:
                        logger.debug(f"Skipping duplicate folder: {full_path}")
                        continue
                    
                    icon = "folder"
                    size_str = ""
                    modified = ""
                    is_local = (sync_dir / full_path).exists()
                    
                    iter = self.file_store.append(parent_iter, [
                        icon, name, size_str, modified, is_local, item_id, True, full_path, ""
                    ])
                    self._folder_iters[full_path] = iter
                    
                else:
                    icon = self._get_file_icon(name)
                    size = self._format_size(item.get('size', 0))
                    modified = item.get('lastModifiedDateTime', '')[:10] if 'lastModifiedDateTime' in item else ''
                    
                    local_path = sync_dir / full_path
                    is_local = local_path.exists()
                    
                    # Use pre-loaded state (not loaded per file!)
                    file_state = files_state.get(full_path, {})
                    error_msg = file_state.get('upload_error', '')
                    
                    self.file_store.append(parent_iter, [
                        icon, name, size, modified, is_local, item_id, False, full_path, error_msg
                    ])
            
            # Update progress
            if total_items > 0:
                progress = int((end_idx / total_items) * 100)
                self._update_status(f"Loading files... {progress}%")
            
            # Schedule next chunk or finalize
            if end_idx < total_items:
                # Use timeout instead of idle_add for better UI responsiveness
                GLib.timeout_add(10, process_chunk, end_idx)
            else:
                # All items processed
                GLib.idle_add(self._finalize_file_list, expanded_paths, scroll_position)
            
            return False  # Don't repeat this idle callback
        
        # Start processing chunks
        GLib.idle_add(process_chunk, 0)
    
    def _ensure_parent_folders(self, parent_path: str, sync_dir: Path):
        """Ensure all parent folders exist in tree, creating them if needed.
        
        Args:
            parent_path: Path to the parent folder
            sync_dir: Sync directory
            
        Returns:
            TreeIter for the parent folder
        """
        parent_path = parent_path.lstrip('/')
        
        # Check if already exists
        if parent_path in self._folder_iters:
            return self._folder_iters[parent_path]
        
        # Split path into parts and ensure each level exists
        parts = Path(parent_path).parts
        current_path = ""
        parent_iter = None
        
        for part in parts:
            if current_path:
                current_path = str(Path(current_path) / part)
            else:
                current_path = part
            
            # Check if this level exists
            if current_path not in self._folder_iters:
                # Create this folder level
                is_local = (sync_dir / current_path).exists()
                iter = self.file_store.append(parent_iter, [
                    "folder", part, "", "", is_local, "", True, current_path, ""
                ])
                self._folder_iters[current_path] = iter
                parent_iter = iter
            else:
                parent_iter = self._folder_iters[current_path]
        
        return parent_iter
    
    def _finalize_file_list(self, expanded_paths, scroll_position):
        """Finalize file list after chunked rendering.
        
        Args:
            expanded_paths: Previously expanded paths to restore
            scroll_position: Previous scroll position to restore
        """
        self._restore_expanded_state(expanded_paths)
        self._restore_scroll_position(scroll_position)
        
        total_items = len(self.remote_files)
        self._update_status(f"Loaded {total_items} items")
        logger.info(f"File tree loaded with {total_items} items")
        
        return False  # Don't repeat this idle callback
    
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
            message: Status message (will be escaped to prevent markup injection)
        """
        # Escape user-controlled data to prevent Pango markup injection
        self.status_label.set_markup(f"<i>Status: {html.escape(message)}</i>")
    
    def _show_error(self, title: str, message: str = None) -> None:
        """Show error dialog.
        
        Args:
            title: Error title (used as message if message is None)
            message: Optional detailed error message
        """
        if message is None:
            message = title
            title = "Error"
            
        DialogHelper.show_error(self, title, message)
