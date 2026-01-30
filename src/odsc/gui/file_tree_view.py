"""File tree view logic for ODSC GUI."""

import logging
from pathlib import Path
from typing import Dict

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gio

logger = logging.getLogger(__name__)


class FileTreeViewMixin:
    """Mixin for file tree view logic, sorting, and tooltips."""
    
    def _init_tree_view_cache(self):
        """Initialize caches for tree view optimizations."""
        self._folder_status_cache = {}
        self._pending_uploads_scanned = False
    
    def _clear_tree_view_cache(self):
        """Clear all tree view caches."""
        self._folder_status_cache = {}
    
    def _render_status_icon(self, column, cell, model, iter, data):
        """Render OneDrive-style status icon.
        
        Args:
            column: TreeViewColumn
            cell: CellRenderer
            model: TreeModel
            iter: TreeIter
            data: User data
        """
        is_local = model.get_value(iter, 4)
        is_folder = model.get_value(iter, 6)
        file_name = model.get_value(iter, 1)
        error_msg = model.get_value(iter, 8)
        
        if is_folder:
            folder_status = self._get_folder_sync_status(model, iter)
            if folder_status == 'all':
                cell.set_property('icon-name', 'emblem-default')
            elif folder_status == 'partial':
                cell.set_property('icon-name', 'emblem-dropbox-selsync')
            elif folder_status == 'none':
                cell.set_property('icon-name', 'weather-overcast')
            else:
                cell.set_property('icon-name', None)
        elif error_msg:
            cell.set_property('icon-name', 'dialog-error')
        elif "(pending upload)" in file_name:
            cell.set_property('icon-name', 'emblem-synchronizing')
        elif is_local:
            cell.set_property('icon-name', 'emblem-default')
        else:
            cell.set_property('icon-name', 'weather-overcast')
    
    def _on_tree_query_tooltip(self, widget, x, y, keyboard_mode, tooltip):
        """Handle tooltip queries for TreeView.
        
        Args:
            widget: TreeView widget
            x: X coordinate (in widget coordinates)
            y: Y coordinate (in widget coordinates)
            keyboard_mode: Whether triggered by keyboard
            tooltip: Tooltip object
            
        Returns:
            True if tooltip should be shown, False otherwise
        """
        bin_x, bin_y = widget.convert_widget_to_bin_window_coords(x, y)
        
        result = widget.get_path_at_pos(bin_x, bin_y)
        if not result:
            return False
        
        path, column, cell_x, cell_y = result
        
        if column != widget.get_column(3):
            return False
        
        model = widget.get_model()
        iter = model.get_iter(path)
        
        is_local = model.get_value(iter, 4)
        is_folder = model.get_value(iter, 6)
        file_name = model.get_value(iter, 1)
        error_msg = model.get_value(iter, 8)
        
        tooltip_text = None
        
        if is_folder:
            folder_status = self._get_folder_sync_status_cached(model, iter)
            if folder_status == 'all':
                tooltip_text = 'All files synced - All files in this folder are available locally'
            elif folder_status == 'partial':
                tooltip_text = 'Partially synced - Some files in this folder are local, some are cloud-only'
            elif folder_status == 'none':
                tooltip_text = 'Cloud-only - No files in this folder are synced locally'
            else:
                tooltip_text = 'Empty folder'
        elif error_msg:
            tooltip_text = f'Upload failed - {error_msg}'
        elif "(pending upload)" in file_name:
            tooltip_text = 'Pending upload - File will be uploaded to OneDrive soon'
        elif is_local:
            tooltip_text = 'Synced - File is available locally and synced with OneDrive'
        else:
            tooltip_text = 'Cloud-only - File is on OneDrive but not downloaded locally'
        
        if tooltip_text:
            tooltip.set_text(tooltip_text)
            return True
        
        return False
    
    def _get_folder_sync_status_cached(self, model, folder_iter):
        """Get cached sync status of a folder.
        
        Args:
            model: TreeModel
            folder_iter: TreeIter for the folder
            
        Returns:
            'all', 'partial', 'none', or 'empty'
        """
        file_path = model.get_value(folder_iter, 7)
        if file_path in self._folder_status_cache:
            return self._folder_status_cache[file_path]
        
        status = self._get_folder_sync_status(model, folder_iter)
        self._folder_status_cache[file_path] = status
        return status
    
    def _get_folder_sync_status(self, model, folder_iter):
        """Get sync status of all files in a folder (recursively).
        
        Args:
            model: TreeModel
            folder_iter: TreeIter for the folder
            
        Returns:
            'all' if all files are synced, 'partial' if some are synced, 
            'none' if no files are synced, 'empty' if no files in folder
        """
        total_files = 0
        synced_files = 0
        
        def count_files(parent_iter):
            nonlocal total_files, synced_files
            
            child_iter = model.iter_children(parent_iter)
            while child_iter:
                is_folder = model.get_value(child_iter, 6)
                
                if is_folder:
                    count_files(child_iter)
                else:
                    total_files += 1
                    is_local = model.get_value(child_iter, 4)
                    if is_local:
                        synced_files += 1
                
                child_iter = model.iter_next(child_iter)
        
        count_files(folder_iter)
        
        if total_files == 0:
            return 'empty'
        elif synced_files == total_files:
            return 'all'
        elif synced_files > 0:
            return 'partial'
        else:
            return 'none'
    
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
        return False
    
    def _update_button_states(self) -> None:
        """Update button enabled/disabled states based on selection."""
        selection = self.file_tree.get_selection()
        model, paths = selection.get_selected_rows()
        
        if not paths:
            self.keep_local_button.set_sensitive(False)
            self.remove_local_button.set_sensitive(False)
            return
        
        has_remote_only = 0
        has_local_copy = 0
        
        for path in paths:
            iter = model.get_iter(path)
            is_local = model.get_value(iter, 4)
            is_folder = model.get_value(iter, 6)
            file_id = model.get_value(iter, 5)
            
            if is_folder:
                folder_local, folder_remote = self._count_folder_files(model, iter)
                has_local_copy += folder_local
                has_remote_only += folder_remote
            else:
                if is_local:
                    has_local_copy += 1
                elif file_id:
                    has_remote_only += 1
        
        self.keep_local_button.set_sensitive(has_remote_only > 0)
        self.remove_local_button.set_sensitive(has_local_copy > 0)
    
    def _count_folder_files(self, model, folder_iter):
        """Count local and remote-only files in a folder recursively.
        
        Args:
            model: TreeModel
            folder_iter: TreeIter for the folder
            
        Returns:
            Tuple of (local_count, remote_only_count)
        """
        local_count = 0
        remote_only_count = 0
        
        def count_files(parent_iter):
            nonlocal local_count, remote_only_count
            
            child_iter = model.iter_children(parent_iter)
            while child_iter:
                is_folder = model.get_value(child_iter, 6)
                
                if is_folder:
                    count_files(child_iter)
                else:
                    is_local = model.get_value(child_iter, 4)
                    file_id = model.get_value(child_iter, 5)
                    
                    if is_local:
                        local_count += 1
                    elif file_id:
                        remote_only_count += 1
                
                child_iter = model.iter_next(child_iter)
        
        count_files(folder_iter)
        return local_count, remote_only_count
    
    def _save_expanded_state(self):
        """Save the list of expanded tree paths.
        
        Returns:
            Set of expanded path tuples
        """
        expanded = set()
        
        def check_row(model, path, iter):
            if self.file_tree.row_expanded(path):
                file_path = model.get_value(iter, 7)
                if file_path:
                    expanded.add(file_path)
        
        self.file_store.foreach(check_row)
        return expanded
    
    def _save_scroll_position(self):
        """Save current scroll position.
        
        Returns:
            Tuple of (hadjustment_value, vadjustment_value)
        """
        scrolled = self.file_tree.get_parent()
        if isinstance(scrolled, Gtk.ScrolledWindow):
            hadj = scrolled.get_hadjustment()
            vadj = scrolled.get_vadjustment()
            return (hadj.get_value(), vadj.get_value())
        return (0, 0)
    
    def _restore_expanded_state(self, expanded_paths):
        """Restore expanded state of tree paths.
        
        Args:
            expanded_paths: Set of file paths that were expanded
        """
        if not expanded_paths:
            self.file_tree.expand_row(Gtk.TreePath.new_first(), False)
            return
        
        def expand_row(model, path, iter):
            file_path = model.get_value(iter, 7)
            if file_path in expanded_paths:
                self.file_tree.expand_row(path, False)
        
        self.file_store.foreach(expand_row)
    
    def _restore_scroll_position(self, position):
        """Restore scroll position.
        
        Args:
            position: Tuple of (hadjustment_value, vadjustment_value)
        """
        if not position:
            return
        
        scrolled = self.file_tree.get_parent()
        if isinstance(scrolled, Gtk.ScrolledWindow):
            hadj = scrolled.get_hadjustment()
            vadj = scrolled.get_vadjustment()
            
            def restore():
                hadj.set_value(position[0])
                vadj.set_value(position[1])
                return False
            
            GLib.timeout_add(50, restore)
    
    def _add_pending_uploads(self, sync_dir: Path, remote_files_set: set, folder_iters: Dict) -> None:
        """Add local files that haven't been uploaded to OneDrive yet (runs in background).
        
        Args:
            sync_dir: Local sync directory
            remote_files_set: Set of paths that exist on OneDrive
            folder_iters: Dictionary of folder path to TreeIter
        """
        if not self._pending_uploads_scanned:
            logger.info("Skipping pending uploads scan during initial load for better performance")
            logger.info("Pending uploads will be detected by the daemon automatically")
            self._pending_uploads_scanned = True
            return
        
        logger.debug("Scanning for pending uploads...")
        pending_count = 0
        
        for path in sync_dir.rglob('*'):
            if any(part.startswith('.') for part in path.parts):
                continue
            
            if path.is_file():
                try:
                    rel_path = str(path.relative_to(sync_dir))
                    
                    if rel_path not in remote_files_set:
                        name = path.name
                        parent_path = str(path.parent.relative_to(sync_dir)) if path.parent != sync_dir else ""
                        
                        parent_iter = None
                        if parent_path and parent_path != '.':
                            parent_iter = folder_iters.get(parent_path)
                            
                            if parent_iter is None and parent_path:
                                logger.debug(f"Parent folder not in tree, skipping: {rel_path}")
                                continue
                        
                        icon = "emblem-synchronizing"
                        size = self._format_size(path.stat().st_size)
                        modified = ""
                        
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
