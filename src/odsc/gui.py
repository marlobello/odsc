#!/usr/bin/env python3
"""GNOME GTK GUI for ODSC."""

import logging
import threading
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import http.server
import socketserver
from urllib.parse import urlparse, parse_qs

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Gio

from .config import Config
from .onedrive_client import OneDriveClient
from .logging_config import setup_logging

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Raised when a security violation is detected."""
    pass


class DialogHelper:
    """Reusable dialog utilities to reduce code duplication."""
    
    @staticmethod
    def show_info(parent, title: str, message: str, secondary: str = "") -> None:
        """Show information dialog."""
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=title
        )
        if secondary:
            dialog.format_secondary_text(secondary)
        dialog.run()
        dialog.destroy()
    
    @staticmethod
    def show_confirm(parent, title: str, message: str) -> bool:
        """Show confirmation dialog. Returns True if user confirms."""
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=title
        )
        dialog.format_secondary_text(message)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.YES
    
    @staticmethod
    def show_error(parent, title: str, message: str) -> None:
        """Show error dialog."""
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()
    
    @staticmethod
    def show_restart_prompt(parent, title: str, message: str) -> bool:
        """Show dialog with restart daemon option. Returns True if user wants to restart."""
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=title
        )
        dialog.format_secondary_text(message)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, 
                          "Restart Daemon", Gtk.ResponseType.YES)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.YES


class AuthCallbackHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler for OAuth callback."""
    
    auth_code = None
    state = None  # For CSRF validation
    
    def do_GET(self):
        """Handle GET request for OAuth callback."""
        parsed = urlparse(self.path)
        if parsed.path == '/':
            params = parse_qs(parsed.query)
            if 'code' in params:
                AuthCallbackHandler.auth_code = params['code'][0]
                AuthCallbackHandler.state = params.get('state', [None])[0]
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


class OneDriveGUI(Gtk.ApplicationWindow):
    """Main GNOME GUI window for OneDrive Sync Client."""
    
    def __init__(self, application):
        """Initialize GUI."""
        Gtk.ApplicationWindow.__init__(self, application=application, title="OneDrive Sync Client")
        
        self.set_default_size(800, 600)
        self.set_border_width(10)
        
        # Set window icon explicitly for both application window and default
        self.set_icon_name("odsc")
        Gtk.Window.set_default_icon_name("odsc")
        
        self.config = Config()
        
        # Setup logging
        setup_logging(level=self.config.log_level, log_file=self.config.log_path)
        logger.info("=== ODSC GUI Starting ===")
        logger.info(f"Config directory: {self.config.config_dir}")
        logger.info(f"Log level: {self.config.log_level}")
        
        self.client: Optional[OneDriveClient] = None
        self.remote_files: List[Dict[str, Any]] = []
        
        # Thread pool for async operations
        self.executor = ThreadPoolExecutor(max_workers=2)
        
        # Menu items that need to be updated based on auth state
        self.login_menu_item: Optional[Gtk.MenuItem] = None
        self.logout_menu_item: Optional[Gtk.MenuItem] = None
        
        # Log panel components
        self.log_panel_visible = False
        self.log_text_view: Optional[Gtk.TextView] = None
        self.log_panel: Optional[Gtk.Box] = None
        self.main_paned: Optional[Gtk.Paned] = None
        self.log_file_position = 0  # Track file position for tailing
        self.log_tail_timer_id = None  # Timer ID for log tailing
        
        # Initialize client if authenticated
        if self.config.load_token():
            logger.info("Found existing token, initializing client")
            self._init_client()
        else:
            logger.info("No existing token found")
        
        self._build_ui()
        
        # Update menu state after UI is built
        self._update_auth_menu_state()
    
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
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)
        
        # Menu bar
        menubar = self._create_menubar()
        vbox.pack_start(menubar, False, False, 0)
        
        # Create paned widget to hold main content and log panel
        self.main_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        vbox.pack_start(self.main_paned, True, True, 0)
        
        # Main content area (top pane)
        main_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.main_paned.pack1(main_content_box, resize=True, shrink=False)
        
        # Main content area with scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        main_content_box.pack_start(scrolled, True, True, 0)
        
        # TreeView for file/folder hierarchy (icon, name, size, modified, local, id, is_folder, path, error)
        self.file_store = Gtk.TreeStore(str, str, str, str, bool, str, bool, str, str)
        self.file_tree = Gtk.TreeView(model=self.file_store)
        self.file_tree.set_enable_tree_lines(True)
        
        # Column 1: Icon + Name (left-aligned)
        column_name = Gtk.TreeViewColumn("Name")
        
        # Icon renderer
        renderer_icon = Gtk.CellRendererPixbuf()
        renderer_icon.set_padding(4, 2)  # horizontal, vertical padding
        column_name.pack_start(renderer_icon, False)
        column_name.add_attribute(renderer_icon, "icon-name", 0)
        
        # Text renderer for name
        renderer_name = Gtk.CellRendererText()
        renderer_name.set_padding(6, 4)  # horizontal, vertical padding
        column_name.pack_start(renderer_name, True)
        column_name.add_attribute(renderer_name, "text", 1)
        column_name.set_resizable(True)
        column_name.set_min_width(300)
        self.file_tree.append_column(column_name)
        
        # Column 2: Size (right-aligned for numbers)
        renderer_size = Gtk.CellRendererText()
        renderer_size.set_padding(8, 4)  # horizontal, vertical padding
        renderer_size.set_alignment(1.0, 0.5)  # right-aligned, vertically centered
        column_size = Gtk.TreeViewColumn("Size", renderer_size, text=2)
        column_size.set_alignment(1.0)  # right-align header too
        column_size.set_resizable(True)
        self.file_tree.append_column(column_size)
        
        # Column 3: Modified (left-aligned for dates)
        renderer_modified = Gtk.CellRendererText()
        renderer_modified.set_padding(8, 4)  # horizontal, vertical padding
        column_modified = Gtk.TreeViewColumn("Modified", renderer_modified, text=3)
        column_modified.set_resizable(True)
        self.file_tree.append_column(column_modified)
        
        # Column 4: Status (centered icon)
        column_status = Gtk.TreeViewColumn("Status")
        column_status.set_alignment(0.5)  # center header
        renderer_status = Gtk.CellRendererPixbuf()
        renderer_status.set_padding(8, 4)  # horizontal, vertical padding
        renderer_status.set_alignment(0.5, 0.5)  # center icon horizontally and vertically
        column_status.pack_start(renderer_status, False)
        column_status.set_cell_data_func(renderer_status, self._render_status_icon)
        self.file_tree.append_column(column_status)
        
        scrolled.add(self.file_tree)
        
        # Connect to selection changed signal for button states
        selection = self.file_tree.get_selection()
        selection.connect("changed", self._on_selection_changed)
        
        # Bottom button bar
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
        
        # Status label - right-justified next to Refresh button
        self.status_label = Gtk.Label()
        self.status_label.set_markup("<i>Status: Ready</i>")
        self.status_label.set_halign(Gtk.Align.END)
        button_box.pack_end(self.status_label, False, False, 6)
        
        # Create log panel (bottom pane) - initially hidden
        self._create_log_panel()
        
        # Service status info bar (created but not shown initially)
        self.service_info_bar = None
        
        # Load files if authenticated
        if self.client:
            self._load_remote_files()
        
        # Check if service is running and notify user if not
        GLib.timeout_add(500, self._check_service_status)
    
    def _render_status_icon(self, column, cell, model, iter, data):
        """Render OneDrive-style status icon.
        
        Args:
            column: TreeViewColumn
            cell: CellRenderer
            model: TreeModel
            iter: TreeIter
            data: User data
        """
        is_local = model.get_value(iter, 4)  # Column 4 is local status
        is_folder = model.get_value(iter, 6)  # Column 6 is folder flag
        file_name = model.get_value(iter, 1)  # Column 1 is name
        error_msg = model.get_value(iter, 8)  # Column 8 is error message
        
        if is_folder:
            # Don't show status icon for folders
            cell.set_property('icon-name', None)
        elif error_msg:
            # Red error icon for failed uploads
            cell.set_property('icon-name', 'dialog-error')
        elif "(pending upload)" in file_name:
            # Blue sync icon for pending uploads
            cell.set_property('icon-name', 'emblem-synchronizing')
        elif is_local:
            # Green checkmark for synced files (local copy exists)
            cell.set_property('icon-name', 'emblem-default')
        else:
            # Overcast cloud icon for online-only files (not downloaded)
            cell.set_property('icon-name', 'weather-overcast')
    
    def _create_log_panel(self) -> None:
        """Create the log panel (initially hidden)."""
        # Create panel container
        self.log_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.log_panel.set_border_width(6)
        
        # Header with title and refresh button
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.log_panel.pack_start(header_box, False, False, 0)
        
        log_label = Gtk.Label()
        log_label.set_markup(f"<b>Log: {self.config.log_path}</b>")
        log_label.set_halign(Gtk.Align.START)
        header_box.pack_start(log_label, True, True, 0)
        
        # Refresh button
        refresh_log_button = Gtk.Button(label="Refresh")
        refresh_log_button.connect("clicked", self._on_refresh_log_clicked)
        header_box.pack_start(refresh_log_button, False, False, 0)
        
        # Auto-scroll toggle
        self.auto_scroll_check = Gtk.CheckButton(label="Auto-scroll")
        self.auto_scroll_check.set_active(True)
        header_box.pack_start(self.auto_scroll_check, False, False, 0)
        
        # Scrolled window with text view
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(200)
        self.log_panel.pack_start(scrolled, True, True, 0)
        
        self.log_text_view = Gtk.TextView()
        self.log_text_view.set_editable(False)
        self.log_text_view.set_monospace(True)
        self.log_text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        scrolled.add(self.log_text_view)
        
        # Don't add to paned yet - will add when shown
    
    def _on_toggle_log_panel(self, widget) -> None:
        """Handle toggle log panel menu item."""
        if widget.get_active():
            self._show_log_panel()
        else:
            self._hide_log_panel()
    
    def _show_log_panel(self) -> None:
        """Show the log panel."""
        if not self.log_panel_visible:
            # Add panel to paned widget
            self.main_paned.pack2(self.log_panel, resize=False, shrink=False)
            self.log_panel.show_all()
            
            # Set paned position (70% for main content, 30% for log)
            window_height = self.get_allocated_height()
            self.main_paned.set_position(int(window_height * 0.7))
            
            # Load log content
            self._refresh_log_content()
            
            # Start log tailing timer (update every 500ms)
            self.log_tail_timer_id = GLib.timeout_add(500, self._tail_log_file)
            
            self.log_panel_visible = True
            logger.info("Log panel shown, tailing started")
    
    def _hide_log_panel(self) -> None:
        """Hide the log panel."""
        if self.log_panel_visible:
            # Stop log tailing timer
            if self.log_tail_timer_id:
                GLib.source_remove(self.log_tail_timer_id)
                self.log_tail_timer_id = None
            
            # Remove panel from paned widget
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
            
            # Auto-scroll to bottom if enabled
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
            return True  # Continue timer, file might be created
        
        try:
            with open(log_path, 'r') as f:
                # Seek to last known position
                f.seek(self.log_file_position)
                
                # Read new content
                new_content = f.read()
                
                if new_content:
                    # Update file position
                    self.log_file_position = f.tell()
                    
                    # Append to text view
                    buffer = self.log_text_view.get_buffer()
                    end_iter = buffer.get_end_iter()
                    buffer.insert(end_iter, new_content)
                    
                    # Auto-scroll to bottom if enabled
                    if self.auto_scroll_check.get_active():
                        GLib.idle_add(self._scroll_log_to_end)
                        
        except Exception as e:
            # Log error but continue tailing
            logger.debug(f"Error tailing log file: {e}")
        
        return True  # Continue the timer
    
    def _scroll_log_to_end(self) -> bool:
        """Scroll log view to the end."""
        buffer = self.log_text_view.get_buffer()
        end_iter = buffer.get_end_iter()
        self.log_text_view.scroll_to_iter(end_iter, 0.0, False, 0.0, 0.0)
        return False
    
    def _restart_daemon(self) -> None:
        """Restart the ODSC daemon using systemctl."""
        import subprocess
        
        logger.info("Attempting to restart daemon via systemctl")
        
        try:
            # Check if daemon is running/enabled first
            result = subprocess.run(
                ['systemctl', '--user', 'is-active', 'odsc'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            daemon_was_running = (result.returncode == 0)
            
            if daemon_was_running:
                # Restart the daemon
                result = subprocess.run(
                    ['systemctl', '--user', 'restart', 'odsc'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    logger.info("Daemon restarted successfully")
                    dialog = Gtk.MessageDialog(
                        transient_for=self,
                        flags=0,
                        message_type=Gtk.MessageType.INFO,
                        buttons=Gtk.ButtonsType.OK,
                        text="Daemon Restarted",
                    )
                    dialog.format_secondary_text(
                        "The ODSC daemon has been restarted successfully.\n"
                        "Your new settings are now active."
                    )
                    dialog.run()
                    dialog.destroy()
                else:
                    logger.error(f"Failed to restart daemon: {result.stderr}")
                    self._show_error(f"Failed to restart daemon:\n{result.stderr}")
            else:
                # Daemon is not running
                logger.info("Daemon is not currently running")
                dialog = Gtk.MessageDialog(
                    transient_for=self,
                    flags=0,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.NONE,
                    text="Daemon Not Running",
                )
                dialog.format_secondary_text(
                    "The ODSC daemon is not currently running.\n\n"
                    "Would you like to start it now?"
                )
                dialog.add_button("No", Gtk.ResponseType.NO)
                dialog.add_button("Start Daemon", Gtk.ResponseType.YES)
                
                response = dialog.run()
                dialog.destroy()
                
                if response == Gtk.ResponseType.YES:
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
        import subprocess
        
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
                dialog = Gtk.MessageDialog(
                    transient_for=self,
                    flags=0,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text="Daemon Started",
                )
                dialog.format_secondary_text(
                    "The ODSC daemon has been started successfully.\n"
                    "Your settings are now active."
                )
                dialog.run()
                dialog.destroy()
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
            
            # If service is not active, show notification
            if result.returncode != 0:
                logger.info("ODSC service is not running")
                self._show_service_not_running_bar()
            else:
                logger.info("ODSC service is running")
                
        except FileNotFoundError:
            # systemctl not available (service might not be installed)
            logger.debug("systemctl not found, skipping service check")
        except Exception as e:
            logger.debug(f"Error checking service status: {e}")
        
        return False  # Don't repeat the timer
    
    def _show_service_not_running_bar(self) -> None:
        """Show an info bar notifying that the service is not running."""
        # Don't show if already shown
        if self.service_info_bar is not None:
            return
        
        # Create info bar
        self.service_info_bar = Gtk.InfoBar()
        self.service_info_bar.set_message_type(Gtk.MessageType.WARNING)
        
        # Add label
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
        
        # Add "Start Service" button
        self.service_info_bar.add_button("Start Service", Gtk.ResponseType.ACCEPT)
        
        # Add "Dismiss" button
        self.service_info_bar.add_button("Dismiss", Gtk.ResponseType.CLOSE)
        
        # Connect response handler
        self.service_info_bar.connect("response", self._on_service_info_bar_response)
        
        # Add to window (get the main vbox - first child of window)
        vbox = self.get_children()[0]
        # Insert after menubar (at position 1)
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
            # User clicked "Start Service"
            self._start_daemon_from_notification()
        
        # Hide and destroy the info bar
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
        
        license_item = Gtk.MenuItem(label="License")
        license_item.connect("activate", self._on_license_clicked)
        help_menu.append(license_item)
        
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
        # Show authentication info dialog (read-only)
        dialog = AuthInfoDialog(self, self.config, self.client)
        dialog.run()
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
        
        # Start local server to receive callback (localhost only for security)
        def wait_for_callback():
            try:
                logger.info("Starting local callback server on localhost:8080")
                with socketserver.TCPServer(("127.0.0.1", 8080), AuthCallbackHandler) as httpd:
                    httpd.timeout = 300  # 5 minute timeout
                    logger.debug("Waiting for OAuth callback...")
                    httpd.handle_request()
                    
                    if AuthCallbackHandler.auth_code:
                        # Validate state parameter for CSRF protection
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
        self._update_auth_menu_state()
        self._load_remote_files()
    
    def _logout(self) -> None:
        """Log out and clear authentication."""
        # Remove token file
        self.config.token_path.unlink(missing_ok=True)
        
        # Clear client
        self.client = None
        
        # Clear file list
        self.file_store.clear()
        
        # Update UI
        self.keep_local_button.set_sensitive(False)
        self.remove_local_button.set_sensitive(False)
        self._update_status("Logged out successfully")
        self._update_auth_menu_state()
        
        # Show info dialog
        DialogHelper.show_info(self, "Logged Out", "You have been logged out successfully.")
    
    def _on_settings_clicked(self, widget) -> None:
        """Handle settings button click."""
        dialog = SettingsDialog(self, self.config)
        dialog.run()
        dialog.destroy()
    
    def _on_force_sync_clicked(self, widget) -> None:
        """Handle force sync menu item click."""
        try:
            # Create force sync signal file
            self.config.force_sync_path.touch()
            logger.info("Force sync signal created")
            
            # Show confirmation dialog
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
        # Open GitHub README in browser
        webbrowser.open("https://github.com/marlobello/odsc/blob/main/README.md")
    
    def _on_license_clicked(self, widget) -> None:
        """Handle License menu item click."""
        # Show license dialog
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text="MIT License",
        )
        dialog.format_secondary_text(
            "Copyright (c) 2026 Marlo Bell\n\n"
            "Permission is hereby granted, free of charge, to any person obtaining a copy "
            "of this software and associated documentation files (the \"Software\"), to deal "
            "in the Software without restriction, including without limitation the rights "
            "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell "
            "copies of the Software, and to permit persons to whom the Software is "
            "furnished to do so, subject to the following conditions:\n\n"
            "The above copyright notice and this permission notice shall be included in all "
            "copies or substantial portions of the Software.\n\n"
            "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR "
            "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, "
            "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE "
            "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER "
            "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, "
            "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE "
            "SOFTWARE."
        )
        dialog.run()
        dialog.destroy()
    
    def _on_selection_changed(self, selection) -> None:
        """Handle selection changed event.
        
        Args:
            selection: TreeSelection object
        """
        self._update_button_states()
    
    def _on_tree_button_press(self, widget, event) -> bool:
        """Handle tree view button press.
        
        Returns:
            True if event handled
        """
        # Selection changed signal will handle button states
        return False
    
    def _update_button_states(self) -> None:
        """Update button enabled/disabled states based on selection."""
        selection = self.file_tree.get_selection()
        model, paths = selection.get_selected_rows()
        
        if not paths:
            self.keep_local_button.set_sensitive(False)
            self.remove_local_button.set_sensitive(False)
            return
        
        # Count files by type (ignore folders)
        has_remote_only = 0  # Files on OneDrive but not local
        has_local_copy = 0   # Files that exist locally
        
        for path in paths:
            iter = model.get_iter(path)
            is_local = model.get_value(iter, 4)
            is_folder = model.get_value(iter, 6)
            file_id = model.get_value(iter, 5)
            
            # Skip folders
            if is_folder:
                continue
            
            # Categorize file
            if is_local:
                # Has local copy (synced)
                has_local_copy += 1
            elif file_id:
                # Has file_id and not local means it's remote-only
                has_remote_only += 1
        
        # Logic:
        # - All selected files are remote-only → Enable "Keep Local Copy"
        # - All selected files have local copies → Enable "Remove Local Copy"
        # - Mixed selection → Disable both buttons
        
        if has_remote_only > 0 and has_local_copy == 0:
            # Only remote-only files selected
            self.keep_local_button.set_sensitive(True)
            self.remove_local_button.set_sensitive(False)
        elif has_local_copy > 0 and has_remote_only == 0:
            # Only synced files selected
            self.keep_local_button.set_sensitive(False)
            self.remove_local_button.set_sensitive(True)
        else:
            # Mixed or no valid files selected
            self.keep_local_button.set_sensitive(False)
            self.remove_local_button.set_sensitive(False)
    
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
        
        # Update button states after action
        GLib.timeout_add(500, self._update_button_states)
    
    def _on_remove_local_clicked(self, widget) -> None:
        """Handle remove local copy button click."""
        selection = self.file_tree.get_selection()
        model, paths = selection.get_selected_rows()
        
        if not paths:
            return
        
        # Confirm deletion
        confirmed = DialogHelper.show_confirm(
            self,
            "Remove Local Copy?",
            f"Remove local copy of {len(paths)} selected file(s)?\n\n"
            "Files will remain on OneDrive and can be downloaded again later."
        )
        
        if not confirmed:
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
        
        # Update button states after action
        GLib.timeout_add(500, self._update_button_states)
    
    def _remove_local_file(self, rel_path: str, file_name: str) -> None:
        """Remove local copy of a file.
        
        Args:
            rel_path: Relative path to file
            file_name: File name for display
        """
        self._update_status(f"Removing local copy of {file_name}...")
        
        def remove_in_thread():
            try:
                # Validate path before removal
                local_path = self._validate_sync_path(rel_path, self.config.sync_directory)
                
                local_path.unlink(missing_ok=True)
                logger.info(f"Removed local copy: {rel_path}")
                
                # Update sync state to mark as not downloaded
                state = self.config.load_state()
                if 'files' in state and rel_path in state['files']:
                    state['files'][rel_path]['downloaded'] = False
                    self.config.save_state(state)
                
                GLib.idle_add(self._update_status, f"Removed local copy of {file_name}")
                GLib.idle_add(self._load_remote_files)  # Refresh
                
            except Exception as e:
                logger.error(f"Failed to remove local copy of {file_name}: {e}")
                GLib.idle_add(self._show_error, f"Failed to remove: {e}")
        
        thread = threading.Thread(target=remove_in_thread, daemon=True)
        thread.start()
    
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
                # Load state to get delta token and cache
                state = self.config.load_state()
                delta_token = state.get('delta_token')
                file_cache = state.get('file_cache', {})
                
                if delta_token and file_cache:
                    # Use incremental sync
                    logger.info("Using delta query for incremental refresh")
                    GLib.idle_add(self._update_status, "Checking for changes...")
                    
                    changes, new_delta_token = self.client.get_delta(delta_token)
                    
                    # Apply changes to cache
                    for item in changes:
                        if item.get('deleted'):
                            # Remove from cache
                            item_id = item['id']
                            for path in list(file_cache.keys()):
                                if file_cache[path].get('id') == item_id:
                                    del file_cache[path]
                                    break
                        else:
                            # Update or add to cache
                            try:
                                parent_path = item.get('parentReference', {}).get('path', '')
                                name = item.get('name', '')
                                
                                if parent_path:
                                    safe_parent = self._sanitize_onedrive_path(parent_path)
                                    full_path = str(Path(safe_parent) / name) if safe_parent else name
                                else:
                                    full_path = name
                                
                                file_cache[full_path] = item
                            except Exception as e:
                                logger.warning(f"Error processing change: {e}")
                    
                    # Update state with new token and cache
                    state['delta_token'] = new_delta_token
                    state['file_cache'] = file_cache
                    self.config.save_state(state)
                    
                    # Build file list from cache
                    # For items without 'name', derive it from the cache key (path)
                    files = []
                    for path, item in file_cache.items():
                        if 'name' not in item and path:
                            # Derive name from path for daemon-created folders
                            item = dict(item)  # Make a copy
                            item['name'] = Path(path).name if path != 'root' else 'OneDrive'
                            item['_cache_path'] = path
                        files.append(item)
                    logger.info(f"Delta refresh complete: {len(changes)} changes, {len(files)} total items")
                else:
                    # Initial load - fetch all files
                    logger.info("Initial load: fetching all files")
                    GLib.idle_add(self._update_status, "Fetching all files (first time)...")
                    
                    changes, new_delta_token = self.client.get_delta(None)
                    
                    # Build cache from initial load
                    file_cache = {}
                    for item in changes:
                        if not item.get('deleted'):
                            try:
                                parent_path = item.get('parentReference', {}).get('path', '')
                                name = item.get('name', '')
                                
                                if parent_path:
                                    safe_parent = self._sanitize_onedrive_path(parent_path)
                                    full_path = str(Path(safe_parent) / name) if safe_parent else name
                                else:
                                    full_path = name
                                
                                file_cache[full_path] = item
                            except Exception as e:
                                logger.warning(f"Error processing item: {e}")
                    
                    # Save initial state
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
    
    def _sanitize_onedrive_path(self, raw_path: str) -> str:
        """Safely extract relative path from OneDrive API path.
        
        Args:
            raw_path: Raw path from OneDrive API
            
        Returns:
            Sanitized relative path safe for local file system
            
        Raises:
            SecurityError: If path contains dangerous components
        """
        # Remove known OneDrive prefixes
        path = raw_path.replace('/drive/root:', '').replace('/drive/root', '')
        
        # Strip leading/trailing slashes to make it a relative path
        path = path.strip('/').strip('\\')
        
        # If empty after stripping, return empty
        if not path:
            return ''
        
        # Use pathlib to properly handle path components
        parts = Path(path).parts
        
        # Filter out dangerous components
        safe_parts = []
        for part in parts:
            # Block path traversal and special names
            # Note: '/' shouldn't appear here anymore due to strip above,
            # but keep it for safety
            if part in ('..', '.', '/', '\\', ''):
                logger.warning(f"Blocked dangerous path component: {part}")
                continue
            # Block absolute paths (shouldn't happen after strip, but double-check)
            if part.startswith('/') or part.startswith('\\'):
                logger.warning(f"Blocked absolute path component: {part}")
                continue
            safe_parts.append(part)
        
        if not safe_parts:
            return ''
        
        return str(Path(*safe_parts))
    
    def _validate_sync_path(self, rel_path: str, sync_dir: Path) -> Path:
        """Validate path is within sync directory and not a symlink.
        
        Args:
            rel_path: Relative path to validate
            sync_dir: Sync directory base path
            
        Returns:
            Validated absolute path
            
        Raises:
            SecurityError: If path validation fails
        """
        # Convert to absolute path
        full_path = (sync_dir / rel_path).resolve()
        sync_dir_resolved = sync_dir.resolve()
        
        # Check it's within sync directory
        try:
            full_path.relative_to(sync_dir_resolved)
        except ValueError:
            raise SecurityError(f"Path traversal detected: {rel_path}")
        
        # Check for symlinks in the path (don't follow them)
        # Start from full_path and work backwards to sync_dir
        check_path = full_path
        while check_path != sync_dir_resolved:
            if check_path.is_symlink():
                raise SecurityError(f"Symlink detected in path: {rel_path}")
            if check_path == check_path.parent:
                # Reached root without finding sync_dir - should not happen
                break
            check_path = check_path.parent
        
        return full_path
    
    def _update_file_list(self, files: List[Dict[str, Any]]) -> None:
        """Update file list view with folder hierarchy.
        
        Args:
            files: List of file metadata from OneDrive
        """
        self.remote_files = files
        self.file_store.clear()
        
        # Update status
        self._update_status(f"Processing {len(files)} items...")
        
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
            # Check both OneDrive format ('folder' key) and daemon format ('is_folder' flag)
            is_folder = 'folder' in item or item.get('is_folder', False)
            item_id = item.get('id', '')
            
            try:
                # Get parent path and sanitize
                parent_ref = item.get('parentReference', {})
                parent_path = parent_ref.get('path', '')
                if parent_path:
                    parent_path = self._sanitize_onedrive_path(parent_path)
                
                # Build full path
                if parent_path:
                    full_path = str(Path(parent_path) / name)
                else:
                    full_path = name
                
                # Validate path is safe
                validated_path = self._validate_sync_path(full_path, sync_dir)
                
                remote_files_set.add(full_path)
            
            except SecurityError as e:
                logger.warning(f"Skipping unsafe path for {name}: {e}")
                continue
            except Exception as e:
                logger.warning(f"Error processing item {name}: {e}")
                continue
            
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
                    icon, name, size_str, modified, is_local, item_id, True, full_path, ""
                ])
                folder_iters[full_path] = iter
                logger.debug(f"Added folder: {full_path}")
                
            else:
                # Add file
                icon = self._get_file_icon(name)
                size = self._format_size(item.get('size', 0))
                modified = item.get('lastModifiedDateTime', '')[:10] if 'lastModifiedDateTime' in item else ''
                
                # Check if file exists locally and get error status
                local_path = sync_dir / full_path
                is_local = local_path.exists()
                
                # Check for upload errors from state
                state = self.config.load_state()
                file_state = state.get('files', {}).get(full_path, {})
                error_msg = file_state.get('upload_error', '')
                
                self.file_store.append(parent_iter, [
                    icon, name, size, modified, is_local, item_id, False, full_path, error_msg
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
                        
                        # Add file with upload icon or error icon
                        icon = "emblem-synchronizing"  # Upload pending icon
                        size = self._format_size(path.stat().st_size)
                        modified = ""
                        
                        # Check for upload errors from state
                        state = self.config.load_state()
                        file_state = state.get('files', {}).get(rel_path, {})
                        error_msg = file_state.get('upload_error', '')
                        
                        self.file_store.append(parent_iter, [
                            icon, f"{name} (pending upload)", size, modified, True, "", False, rel_path, error_msg
                        ])
                        pending_count += 1
                        logger.debug(f"Added pending upload: {rel_path}")
                        
                except (OSError, ValueError) as e:
                    logger.warning(f"Cannot process {path}: {e}")
        
        if pending_count > 0:
            logger.info(f"Found {pending_count} files pending upload")
    
    def _get_file_icon(self, filename: str) -> str:
        """Get icon name for file type using GIO content type detection.
        
        Args:
            filename: File name
            
        Returns:
            GTK icon name
        """
        content_type, _ = Gio.content_type_guess(filename, None)
        if content_type:
            icon = Gio.content_type_get_icon(content_type)
            names = icon.get_names() if hasattr(icon, 'get_names') else []
            return names[0] if names else 'text-x-generic'
        return 'text-x-generic'
    
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
                    parent_path = self._sanitize_onedrive_path(parent_path)
                
                rel_path = str(Path(parent_path) / file_name) if parent_path else file_name
                
                # Validate path before download
                local_path = self._validate_sync_path(rel_path, self.config.sync_directory)
                
                # Download file with retry logic
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
                    'upload_error': None,  # Clear any previous error
                }
                self.config.save_state(state)
                
                logger.info(f"Downloaded and marked for sync: {rel_path}")
                GLib.idle_add(self._update_status, f"Downloaded {file_name}")
                GLib.idle_add(self._load_remote_files)  # Refresh to update local status
            except Exception as e:
                error_msg = f"Failed to download {file_name}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                GLib.idle_add(self._show_error, "Download Failed", error_msg)
                GLib.idle_add(self._update_status, f"Download failed: {file_name}")
        
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
    
    def _show_error(self, title: str, message: str = None) -> None:
        """Show error dialog.
        
        Args:
            title: Error title (used as message if message is None)
            message: Optional detailed error message
        """
        # Support both old and new calling styles
        if message is None:
            message = title
            title = "Error"
            
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title,
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
            
            # Get user info from API
            user_info = None
            try:
                if client:
                    user_info = client.get_user_info()
            except Exception as e:
                logger.warning(f"Could not fetch user info: {e}")
            
            # User Name (if available)
            if user_info and 'displayName' in user_info:
                name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                name_label = Gtk.Label(label="User Name:")
                name_label.set_width_chars(15)
                name_label.set_halign(Gtk.Align.START)
                name_value = Gtk.Label(label=user_info['displayName'])
                name_value.set_halign(Gtk.Align.START)
                name_box.pack_start(name_label, False, False, 0)
                name_box.pack_start(name_value, False, False, 0)
                box.add(name_box)
            
            # Email (if available)
            if user_info:
                email = user_info.get('mail') or user_info.get('userPrincipalName')
                if email:
                    email_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    email_label = Gtk.Label(label="Email:")
                    email_label.set_width_chars(15)
                    email_label.set_halign(Gtk.Align.START)
                    email_value = Gtk.Label(label=email)
                    email_value.set_halign(Gtk.Align.START)
                    email_value.set_selectable(True)
                    email_box.pack_start(email_label, False, False, 0)
                    email_box.pack_start(email_value, False, False, 0)
                    box.add(email_box)
            
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
            
            # Last Login Time
            if token_data and 'expires_at' in token_data and 'expires_in' in token_data:
                import time
                from datetime import datetime
                # Calculate when token was created (expires_at - expires_in)
                expires_at = token_data['expires_at']
                expires_in = token_data['expires_in']
                created_at = expires_at - expires_in
                login_datetime = datetime.fromtimestamp(created_at)
                
                login_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                login_label = Gtk.Label(label="Last Login:")
                login_label.set_width_chars(15)
                login_label.set_halign(Gtk.Align.START)
                login_value = Gtk.Label(label=login_datetime.strftime('%Y-%m-%d %H:%M:%S'))
                login_value.set_halign(Gtk.Align.START)
                login_box.pack_start(login_label, False, False, 0)
                login_box.pack_start(login_value, False, False, 0)
                box.add(login_box)
            
            # Application ID
            client_id_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            client_id_label = Gtk.Label(label="Application ID:")
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
            
            # Buttons - just Close
            self.add_button("Close", Gtk.ResponseType.CLOSE)
            
        else:
            # Not authenticated
            title_label = Gtk.Label()
            title_label.set_markup("<b>Not Authenticated</b>")
            title_label.set_halign(Gtk.Align.START)
            box.add(title_label)
            
            info_label = Gtk.Label(
                label="You are not currently authenticated with OneDrive.\n\n"
                      "Use Authentication → Login to log in with your Microsoft account."
            )
            info_label.set_halign(Gtk.Align.START)
            info_label.set_line_wrap(True)
            box.add(info_label)
            
            # Show application ID that will be used
            client_id = config.client_id or OneDriveClient.DEFAULT_CLIENT_ID
            client_id_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            client_id_label = Gtk.Label(label="Application ID:")
            client_id_label.set_width_chars(15)
            client_id_label.set_halign(Gtk.Align.START)
            client_id_value = Gtk.Label(label=client_id)
            client_id_value.set_halign(Gtk.Align.START)
            client_id_value.set_selectable(True)
            client_id_box.pack_start(client_id_label, False, False, 0)
            client_id_box.pack_start(client_id_value, False, False, 0)
            box.add(client_id_box)
            
            # Buttons - just Close
            self.add_button("Close", Gtk.ResponseType.CLOSE)
        
        self.show_all()


class SettingsDialog(Gtk.Dialog):
    """Settings dialog."""
    
    def __init__(self, parent, config: Config):
        """Initialize dialog."""
        Gtk.Dialog.__init__(self, title="Settings", transient_for=parent, flags=0)
        self.add_buttons("Close", Gtk.ResponseType.CLOSE)
        
        self.parent_window = parent
        self.config = config
        self.set_default_size(400, 200)
        
        # Track if we're initializing to avoid triggering change handlers
        self._initializing = True
        
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
        
        adjustment = Gtk.Adjustment(value=config.sync_interval, lower=60, upper=86400, step_increment=60)
        self.interval_spin = Gtk.SpinButton(adjustment=adjustment)
        self.interval_spin.connect("value-changed", self._on_interval_changed)
        hbox2.pack_start(self.interval_spin, False, False, 0)
        
        box.add(hbox2)
        
        # Log level
        hbox3 = Gtk.Box(spacing=6)
        label3 = Gtk.Label(label="Log Level:")
        label3.set_width_chars(20)
        label3.set_halign(Gtk.Align.START)
        hbox3.pack_start(label3, False, False, 0)
        
        # Create combo box for log levels using ComboBoxText
        self.log_level_combo = Gtk.ComboBoxText()
        self.log_level_combo.set_entry_text_column(0)
        log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        for level in log_levels:
            self.log_level_combo.append_text(level)
        
        # Set current log level
        current_level = config.log_level
        for i, level in enumerate(log_levels):
            if level == current_level:
                self.log_level_combo.set_active(i)
                break
        
        # Connect to changed signal AFTER setting initial value to avoid triggering on init
        self.log_level_combo.connect("changed", self._on_log_level_changed)
        hbox3.pack_start(self.log_level_combo, False, False, 0)
        
        box.add(hbox3)
        
        # Mark initialization as complete
        self._initializing = False
        
        self.show_all()
    
    def _on_sync_dir_changed(self, widget) -> None:
        """Handle sync directory change."""
        if self._initializing:
            return
            
        path = widget.get_filename()
        if path:
            old_dir = self.config.sync_directory
            new_dir = Path(path)
            
            try:
                # Validate and save to config
                self.config.set('sync_directory', str(new_dir))
                logger.info(f"Sync directory changed from {old_dir} to {new_dir}")
                
                # Show confirmation with daemon restart option
                dialog = Gtk.MessageDialog(
                    transient_for=self.get_transient_for(),
                    flags=0,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.NONE,
                    text="Sync Directory Changed",
                )
                dialog.format_secondary_text(
                    f"Sync directory changed to:\n{new_dir}\n\n"
                    "The daemon needs to be restarted for this change to take effect."
                )
                dialog.add_button("Close", Gtk.ResponseType.CLOSE)
                dialog.add_button("Restart Daemon", Gtk.ResponseType.APPLY)
                
                response = dialog.run()
                dialog.destroy()
                
                if response == Gtk.ResponseType.APPLY:
                    self.parent_window._restart_daemon()
                    
            except ValueError as e:
                # Validation failed
                DialogHelper.show_error(self, f"Invalid sync directory: {e}")
                # Revert to old value
                self.sync_dir_button.set_filename(str(old_dir))
    
    def _on_interval_changed(self, widget) -> None:
        """Handle sync interval change."""
        if self._initializing:
            return
            
        value = int(widget.get_value())
        
        try:
            self.config.set('sync_interval', value)
            logger.info(f"Sync interval changed to {value} seconds")
            
            # Show confirmation with daemon restart option
            dialog = Gtk.MessageDialog(
                transient_for=self.get_transient_for(),
                flags=0,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.NONE,
                text="Sync Interval Changed",
            )
            dialog.format_secondary_text(
                f"Sync interval changed to {value} seconds.\n\n"
                "The daemon needs to be restarted for this change to take effect."
            )
            dialog.add_button("Close", Gtk.ResponseType.CLOSE)
            dialog.add_button("Restart Daemon", Gtk.ResponseType.APPLY)
            
            response = dialog.run()
            dialog.destroy()
            
            if response == Gtk.ResponseType.APPLY:
                self.parent_window._restart_daemon()
                
        except ValueError as e:
            # Validation failed
            DialogHelper.show_error(self, f"Invalid sync interval: {e}")
            # Revert to old value
            self.interval_spin.set_value(self.config.sync_interval)
    
    def _on_log_level_changed(self, widget) -> None:
        """Handle log level change."""
        if self._initializing:
            return
            
        log_level = widget.get_active_text()
        if log_level:
            try:
                # Validate and save to config
                self.config.set('log_level', log_level)
                
                # Apply the log level immediately
                setup_logging(level=log_level, log_file=self.config.log_path)
                logger.info(f"Log level changed to {log_level} via GUI")
                
                # Show confirmation dialog
                dialog = Gtk.MessageDialog(
                    transient_for=self.get_transient_for(),
                    flags=0,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text="Log Level Changed",
                )
                dialog.format_secondary_text(
                    f"Log level changed to {log_level}.\n\n"
                    "The new log level is now active."
                )
                dialog.run()
                dialog.destroy()
                
            except ValueError as e:
                # Validation failed
                DialogHelper.show_error(self, f"Invalid log level: {e}")
                # Revert to old value
                old_level = self.config.log_level
                log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
                for i, level in enumerate(log_levels):
                    if level == old_level:
                        self.log_level_combo.set_active(i)
                        break


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


def main():
    """Main entry point for GUI."""
    app = OneDriveApplication()
    app.run(None)


if __name__ == '__main__':
    main()
