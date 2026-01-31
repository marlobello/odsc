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
        self.set_default_size(550, 400)
        self.set_border_width(0)
        
        box = self.get_content_area()
        box.set_spacing(0)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(24)
        box.set_margin_end(24)
        
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        main_box.set_halign(Gtk.Align.CENTER)
        main_box.set_size_request(500, -1)
        box.pack_start(main_box, True, True, 0)
        
        # Check if authenticated
        token_data = config.load_token()
        is_authenticated = token_data is not None and client is not None
        
        if is_authenticated:
            # Get user info from API
            user_info = None
            try:
                if client:
                    user_info = client.get_user_info()
            except Exception as e:
                logger.warning(f"Could not fetch user info: {e}")
            
            # Account Information Group
            account_group = self._create_auth_group(
                "Account Information",
                "Microsoft account details"
            )
            main_box.pack_start(account_group, False, False, 0)
            
            # User Name
            if user_info and 'displayName' in user_info:
                name_row = self._create_info_row(
                    "User Name",
                    html.escape(user_info['displayName'])
                )
                account_group.add(name_row)
            
            # Email
            if user_info:
                email = user_info.get('mail') or user_info.get('userPrincipalName')
                if email:
                    email_row = self._create_info_row(
                        "Email",
                        html.escape(email),
                        selectable=True
                    )
                    account_group.add(email_row)
            
            # Status
            status_row = self._create_info_row(
                "Status",
                "✓ Authenticated"
            )
            account_group.add(status_row)
            
            # Session Information Group
            session_group = self._create_auth_group(
                "Session Information",
                "Token and authentication status"
            )
            main_box.pack_start(session_group, False, False, 0)
            
            # Last Login Time
            if token_data and 'expires_at' in token_data and 'expires_in' in token_data:
                import time
                from datetime import datetime
                expires_at = token_data['expires_at']
                expires_in = token_data['expires_in']
                created_at = expires_at - expires_in
                login_datetime = datetime.fromtimestamp(created_at)
                
                login_row = self._create_info_row(
                    "Last Login",
                    login_datetime.strftime('%Y-%m-%d %H:%M:%S')
                )
                session_group.add(login_row)
            
            # Token expiry info
            if token_data and 'expires_at' in token_data:
                import time
                from datetime import datetime
                expires_at = token_data['expires_at']
                expires_datetime = datetime.fromtimestamp(expires_at)
                time_remaining = expires_at - time.time()
                
                if time_remaining > 0:
                    hours = int(time_remaining / 3600)
                    expiry_text = f"{expires_datetime.strftime('%Y-%m-%d %H:%M')} ({hours}h remaining)"
                else:
                    expiry_text = "Expired (will auto-refresh)"
                
                expiry_row = self._create_info_row(
                    "Token Expires",
                    expiry_text
                )
                session_group.add(expiry_row)
            
            # Has refresh token?
            if token_data and 'refresh_token' in token_data:
                refresh_row = self._create_info_row(
                    "Refresh Token",
                    "✓ Available"
                )
                session_group.add(refresh_row)
            
            # Token file location
            token_row = self._create_info_row(
                "Token File",
                str(config.token_path),
                selectable=True,
                wrap=True
            )
            session_group.add(token_row)
            
            # Buttons
            self.add_button("_Close", Gtk.ResponseType.CLOSE)
            
        else:
            # Not authenticated - show info message
            not_auth_group = self._create_auth_group(
                "Not Authenticated",
                "You are not currently authenticated with OneDrive"
            )
            main_box.pack_start(not_auth_group, False, False, 0)
            
            info_row = self._create_info_row(
                "Action Required",
                "Use Authentication → Login to authenticate with your Microsoft account",
                wrap=True
            )
            not_auth_group.add(info_row)
            
            # Buttons
            self.add_button("_Close", Gtk.ResponseType.CLOSE)
        
        self.show_all()
    
    def _create_auth_group(self, title: str, description: str) -> Gtk.Box:
        """Create a Libadwaita-style group for authentication dialog."""
        group_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        header_box.set_margin_start(12)
        
        title_label = Gtk.Label(label=title)
        title_label.set_halign(Gtk.Align.START)
        title_label.get_style_context().add_class("heading")
        header_box.pack_start(title_label, False, False, 0)
        
        desc_label = Gtk.Label(label=description)
        desc_label.set_halign(Gtk.Align.START)
        desc_label.get_style_context().add_class("dim-label")
        desc_label.get_style_context().add_class("caption")
        header_box.pack_start(desc_label, False, False, 0)
        
        group_box.pack_start(header_box, False, False, 0)
        
        # Boxed list frame
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.get_style_context().add_class("view")
        
        list_box = Gtk.ListBox()
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        frame.add(list_box)
        
        group_box.pack_start(frame, False, False, 0)
        
        # Store list_box reference
        group_box.list_box = list_box
        
        return group_box
    
    def _create_info_row(self, label: str, value: str, selectable: bool = False, wrap: bool = False) -> Gtk.Box:
        """Create an info row for authentication dialog."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_top(12)
        row.set_margin_bottom(12)
        row.set_margin_start(12)
        row.set_margin_end(12)
        
        # Label
        label_widget = Gtk.Label(label=label)
        label_widget.set_halign(Gtk.Align.START)
        label_widget.set_xalign(0)
        label_widget.set_valign(Gtk.Align.START)
        label_widget.set_width_chars(15)
        row.pack_start(label_widget, False, False, 0)
        
        # Value
        value_widget = Gtk.Label(label=value)
        value_widget.set_halign(Gtk.Align.START)
        value_widget.set_xalign(0)
        value_widget.set_selectable(selectable)
        if wrap:
            value_widget.set_line_wrap(True)
            value_widget.set_max_width_chars(50)
        row.pack_start(value_widget, True, True, 0)
        
        return row


class SettingsDialog(Gtk.Dialog):
    """Libadwaita-style preferences dialog using GTK3."""
    
    def __init__(self, parent, config: Config):
        """Initialize dialog."""
        Gtk.Dialog.__init__(self, title="Preferences", transient_for=parent, flags=0)
        self.add_buttons("_Close", Gtk.ResponseType.CLOSE)
        
        self.parent_window = parent
        self.config = config
        self.set_default_size(600, 400)
        self.set_border_width(0)  # No border for modern look
        
        # Track if we're initializing to avoid triggering change handlers
        self._initializing = True
        
        box = self.get_content_area()
        box.set_spacing(0)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(24)
        box.set_margin_end(24)
        
        # Main container (no scrolling needed)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        main_box.set_halign(Gtk.Align.CENTER)
        main_box.set_size_request(540, -1)  # Clamp width like Adw
        box.pack_start(main_box, True, True, 0)
        
        # Sync Settings Group
        sync_group = self._create_preferences_group(
            "Sync Settings",
            "Configure synchronization behavior"
        )
        main_box.pack_start(sync_group, False, False, 0)
        
        # Add sync directory row
        sync_dir_row = self._create_action_row(
            "Sync Directory",
            str(config.sync_directory)
        )
        dir_button = Gtk.Button(label="Choose…")
        dir_button.connect("clicked", self._on_choose_directory)
        dir_button.set_valign(Gtk.Align.CENTER)
        sync_dir_row.pack_end(dir_button, False, False, 0)
        self.sync_dir_subtitle = sync_dir_row.get_children()[0].get_children()[1]
        sync_group.add(sync_dir_row)
        
        # Add sync interval row
        interval_row = self._create_action_row(
            "Sync Interval (seconds)",
            "Time between synchronization checks"
        )
        adjustment = Gtk.Adjustment(value=config.sync_interval, lower=60, upper=86400, step_increment=60)
        self.interval_spin = Gtk.SpinButton(adjustment=adjustment)
        self.interval_spin.set_valign(Gtk.Align.CENTER)
        self.interval_spin.connect("value-changed", self._on_interval_changed)
        
        interval_row.pack_end(self.interval_spin, False, False, 0)
        sync_group.add(interval_row)
        
        # Application Settings Group
        app_group = self._create_preferences_group(
            "Application",
            "General application preferences"
        )
        main_box.pack_start(app_group, False, False, 0)
        
        # Add log level row
        log_level_row = self._create_action_row(
            "Log Level",
            "Detail level for log messages"
        )
        
        self.log_level_combo = Gtk.ComboBoxText()
        self.log_level_combo.set_valign(Gtk.Align.CENTER)
        log_level_names = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        for level in log_level_names:
            self.log_level_combo.append_text(level)
        
        # Set current log level
        current_level = config.log_level
        for i, level in enumerate(log_level_names):
            if level == current_level:
                self.log_level_combo.set_active(i)
                break
        
        self.log_level_combo.connect("changed", self._on_log_level_changed)
        log_level_row.pack_end(self.log_level_combo, False, False, 0)
        app_group.add(log_level_row)
        
        # Add splash screen switch row
        splash_row = self._create_switch_row(
            "Splash Screen",
            "Display splash screen on startup"
        )
        self.splash_switch = Gtk.Switch()
        self.splash_switch.set_valign(Gtk.Align.CENTER)
        self.splash_switch.set_active(config.show_splash)
        self.splash_switch.connect("notify::active", self._on_show_splash_changed)
        splash_row.pack_end(self.splash_switch, False, False, 0)
        app_group.add(splash_row)
        
        # Mark initialization as complete
        self._initializing = False
        
        self.show_all()
    
    def _create_preferences_group(self, title: str, description: str) -> Gtk.Box:
        """Create a Libadwaita-style preferences group (boxed list)."""
        group_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        header_box.set_margin_start(12)
        
        title_label = Gtk.Label(label=title)
        title_label.set_halign(Gtk.Align.START)
        title_label.get_style_context().add_class("heading")
        header_box.pack_start(title_label, False, False, 0)
        
        desc_label = Gtk.Label(label=description)
        desc_label.set_halign(Gtk.Align.START)
        desc_label.get_style_context().add_class("dim-label")
        desc_label.get_style_context().add_class("caption")
        header_box.pack_start(desc_label, False, False, 0)
        
        group_box.pack_start(header_box, False, False, 0)
        
        # Boxed list frame
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.get_style_context().add_class("view")
        
        list_box = Gtk.ListBox()
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        frame.add(list_box)
        
        group_box.pack_start(frame, False, False, 0)
        
        # Store list_box reference so we can add rows
        group_box.list_box = list_box
        
        return group_box
    
    def _create_action_row(self, title: str, subtitle: str) -> Gtk.Box:
        """Create a Libadwaita-style action row."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_top(12)
        row.set_margin_bottom(12)
        row.set_margin_start(12)
        row.set_margin_end(12)
        
        # Left side: title and subtitle
        labels_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        labels_box.set_valign(Gtk.Align.CENTER)
        
        title_label = Gtk.Label(label=title)
        title_label.set_halign(Gtk.Align.START)
        title_label.set_xalign(0)
        labels_box.pack_start(title_label, False, False, 0)
        
        subtitle_label = Gtk.Label(label=subtitle)
        subtitle_label.set_halign(Gtk.Align.START)
        subtitle_label.set_xalign(0)
        subtitle_label.get_style_context().add_class("dim-label")
        subtitle_label.get_style_context().add_class("caption")
        labels_box.pack_start(subtitle_label, False, False, 0)
        
        row.pack_start(labels_box, True, True, 0)
        
        return row
    
    def _create_switch_row(self, title: str, subtitle: str) -> Gtk.Box:
        """Create a Libadwaita-style switch row."""
        return self._create_action_row(title, subtitle)
    
    def _on_choose_directory(self, button) -> None:
        """Handle sync directory selection."""
        if self._initializing:
            return
        
        dialog = Gtk.FileChooserDialog(
            title="Select Sync Directory",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            buttons=(
                "_Cancel", Gtk.ResponseType.CANCEL,
                "_Select", Gtk.ResponseType.ACCEPT
            )
        )
        dialog.set_current_folder(str(self.config.sync_directory))
        
        response = dialog.run()
        path = dialog.get_filename()
        dialog.destroy()
        
        if response == Gtk.ResponseType.ACCEPT and path:
            old_dir = self.config.sync_directory
            new_dir = Path(path)
            
            try:
                # Validate and save to config
                self.config.set('sync_directory', str(new_dir))
                self.sync_dir_subtitle.set_text(str(new_dir))
                logger.info(f"Sync directory changed from {old_dir} to {new_dir}")
                
                # Show confirmation with daemon restart option
                if DialogHelper.show_restart_prompt(
                    self.parent_window,
                    "Sync Directory Changed",
                    f"Sync directory changed to:\n{new_dir}\n\n"
                    "The daemon needs to be restarted for this change to take effect."
                ):
                    self.parent_window._restart_daemon()
                    
            except ValueError as e:
                # Validation failed
                DialogHelper.show_error(self.parent_window, f"Invalid sync directory: {e}")
                # Revert to old value
                self.sync_dir_subtitle.set_text(str(old_dir))
    
    def _on_interval_changed(self, widget) -> None:
        """Handle sync interval change."""
        if self._initializing:
            return
            
        value = int(widget.get_value())
        
        try:
            self.config.set('sync_interval', value)
            logger.info(f"Sync interval changed to {value} seconds")
            
            # Show confirmation with daemon restart option
            if DialogHelper.show_restart_prompt(
                self.parent_window,
                "Sync Interval Changed",
                f"Sync interval changed to {value} seconds.\n\n"
                "The daemon needs to be restarted for this change to take effect."
            ):
                self.parent_window._restart_daemon()
                
        except ValueError as e:
            # Validation failed
            DialogHelper.show_error(self.parent_window, f"Invalid sync interval: {e}")
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
                logger.info(f"Log level changed to {log_level}")
                
                # Show confirmation with daemon restart option
                if DialogHelper.show_restart_prompt(
                    self.parent_window,
                    "Log Level Changed",
                    f"Log level changed to {log_level}.\n\n"
                    "The daemon needs to be restarted for this change to take effect."
                ):
                    self.parent_window._restart_daemon()
                    
            except ValueError as e:
                # Validation failed
                DialogHelper.show_error(self.parent_window, f"Invalid log level: {e}")
    
    def _on_show_splash_changed(self, widget, _pspec) -> None:
        """Handle show splash screen toggle."""
        if self._initializing:
            return
        
        show_splash = self.splash_switch.get_active()
        
        try:
            self.config.set('show_splash', show_splash)
            status = "enabled" if show_splash else "disabled"
            logger.info(f"Splash screen {status}")
            
            DialogHelper.show_info(
                self.parent_window,
                "Splash Screen Setting Changed",
                f"Splash screen has been {status}.\n\n"
                f"This will take effect the next time you launch the GUI."
            )
        except ValueError as e:
            # Validation failed (unlikely for boolean)
            DialogHelper.show_error(self, f"Failed to save setting: {e}")
            # Revert
            widget.set_active(self.config.show_splash)
