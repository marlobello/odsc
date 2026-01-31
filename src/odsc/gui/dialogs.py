"""Dialog classes for ODSC GUI."""

import html
import logging
from pathlib import Path
from typing import Optional

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from ..config import Config
from ..onedrive_client import OneDriveClient
from ..logging_config import setup_logging

logger = logging.getLogger(__name__)


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
            # Escape client ID (though it's controlled, be defensive)
            client_id_value = Gtk.Label(label=html.escape(client.client_id if client else "Unknown"))
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
        label1.set_halign(Gtk.Align.END)
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
        label2.set_halign(Gtk.Align.END)
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
        label3.set_halign(Gtk.Align.END)
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
        
        # Show splash screen checkbox
        hbox4 = Gtk.Box(spacing=6)
        label4 = Gtk.Label(label="Splash Screen:")
        label4.set_width_chars(20)
        label4.set_halign(Gtk.Align.END)
        hbox4.pack_start(label4, False, False, 0)
        
        self.show_splash_check = Gtk.CheckButton(label="Display on startup")
        self.show_splash_check.set_active(config.show_splash)
        self.show_splash_check.connect("toggled", self._on_show_splash_changed)
        hbox4.pack_start(self.show_splash_check, False, False, 0)
        
        box.add(hbox4)
        
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
                if DialogHelper.show_restart_prompt(
                    self.get_transient_for(),
                    "Sync Directory Changed",
                    f"Sync directory changed to:\n{new_dir}\n\n"
                    "The daemon needs to be restarted for this change to take effect."
                ):
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
            # Show confirmation with daemon restart option
            if DialogHelper.show_restart_prompt(
                self.get_transient_for(),
                "Sync Interval Changed",
                f"Sync interval changed to {value} seconds.\n\n"
                "The daemon needs to be restarted for this change to take effect."
            ):
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
                
                # Apply the log level immediately to GUI
                setup_logging(level=log_level, log_file=self.config.log_path)
                logger.info(f"Log level changed to {log_level} via GUI")
                
                # Show confirmation with daemon restart option
                if DialogHelper.show_restart_prompt(
                    self.get_transient_for(),
                    "Log Level Changed",
                    f"Log level changed to {log_level}.\n\n"
                    "The GUI is now using the new log level.\n"
                    "The daemon needs to be restarted to apply the change."
                ):
                    self.parent_window._restart_daemon()
                
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
    
    def _on_show_splash_changed(self, widget) -> None:
        """Handle show splash screen toggle."""
        if self._initializing:
            return
        
        show_splash = widget.get_active()
        
        try:
            self.config.set('show_splash', show_splash)
            status = "enabled" if show_splash else "disabled"
            logger.info(f"Splash screen {status}")
            
            DialogHelper.show_info(
                self.get_transient_for(),
                "Splash Screen Setting Changed",
                f"Splash screen has been {status}.\n\n"
                f"This will take effect the next time you launch the GUI."
            )
        except ValueError as e:
            # Validation failed (unlikely for boolean)
            DialogHelper.show_error(self, f"Failed to save setting: {e}")
            # Revert
            widget.set_active(self.config.show_splash)
