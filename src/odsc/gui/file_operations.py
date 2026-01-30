"""File operation handlers for ODSC GUI."""

import logging
import threading
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from ..path_utils import sanitize_onedrive_path, validate_sync_path, cleanup_empty_parent_dirs
from .dialogs import DialogHelper

logger = logging.getLogger(__name__)


class FileOperationsMixin:
    """Mixin for file download, upload, and remove operations."""
    
    def _on_keep_local_clicked(self, widget) -> None:
        """Handle keep local copy button click."""
        selection = self.file_tree.get_selection()
        model, paths = selection.get_selected_rows()
        
        if not paths:
            return
        
        files_to_download = []
        
        for path in paths:
            iter = model.get_iter(path)
            is_folder = model.get_value(iter, 6)
            
            if is_folder:
                files_to_download.extend(self._get_all_files_in_folder(model, iter))
            else:
                file_id = model.get_value(iter, 5)
                is_local = model.get_value(iter, 4)
                file_name = model.get_value(iter, 1)
                
                if not is_local and file_id:
                    files_to_download.append((file_id, file_name))
        
        if not files_to_download:
            return
        
        if len(files_to_download) > 10:
            self._download_files_batch(files_to_download)
        else:
            for file_id, file_name in files_to_download:
                self._download_file(file_id, file_name)
            
            GLib.timeout_add(500, self._update_button_states)
    
    def _on_remove_local_clicked(self, widget) -> None:
        """Handle remove local copy button click."""
        selection = self.file_tree.get_selection()
        model, paths = selection.get_selected_rows()
        
        if not paths:
            return
        
        files_to_remove = []
        
        for path in paths:
            iter = model.get_iter(path)
            is_folder = model.get_value(iter, 6)
            
            if is_folder:
                files_to_remove.extend(self._get_all_files_in_folder_for_removal(model, iter))
            else:
                file_path_str = model.get_value(iter, 7)
                is_local = model.get_value(iter, 4)
                file_name = model.get_value(iter, 1)
                
                if is_local:
                    files_to_remove.append((file_path_str, file_name))
        
        if not files_to_remove:
            return
        
        confirmed = DialogHelper.show_confirm(
            self,
            "Remove Local Copy?",
            f"Remove local copy of {len(files_to_remove)} file(s)?\n\n"
            "Files will remain on OneDrive and can be downloaded again later."
        )
        
        if not confirmed:
            return
        
        for file_path_str, file_name in files_to_remove:
            self._remove_local_file(file_path_str, file_name)
        
        GLib.timeout_add(500, self._update_button_states)
    
    def _get_all_files_in_folder(self, model, folder_iter):
        """Get all files in a folder recursively for downloading.
        
        Args:
            model: TreeModel
            folder_iter: TreeIter for the folder
            
        Returns:
            List of tuples (file_id, file_name) for files that aren't local
        """
        files = []
        
        def collect_files(parent_iter):
            child_iter = model.iter_children(parent_iter)
            while child_iter:
                is_folder = model.get_value(child_iter, 6)
                
                if is_folder:
                    collect_files(child_iter)
                else:
                    file_id = model.get_value(child_iter, 5)
                    is_local = model.get_value(child_iter, 4)
                    file_name = model.get_value(child_iter, 1)
                    
                    if not is_local and file_id:
                        files.append((file_id, file_name))
                
                child_iter = model.iter_next(child_iter)
        
        collect_files(folder_iter)
        return files
    
    def _get_all_files_in_folder_for_removal(self, model, folder_iter):
        """Get all files in a folder recursively for removal.
        
        Args:
            model: TreeModel
            folder_iter: TreeIter for the folder
            
        Returns:
            List of tuples (file_path, file_name) for files that are local
        """
        files = []
        
        def collect_files(parent_iter):
            child_iter = model.iter_children(parent_iter)
            while child_iter:
                is_folder = model.get_value(child_iter, 6)
                
                if is_folder:
                    collect_files(child_iter)
                else:
                    file_path_str = model.get_value(child_iter, 7)
                    is_local = model.get_value(child_iter, 4)
                    file_name = model.get_value(child_iter, 1)
                    
                    if is_local:
                        files.append((file_path_str, file_name))
                
                child_iter = model.iter_next(child_iter)
        
        collect_files(folder_iter)
        return files
    
    def _remove_local_file(self, rel_path: str, file_name: str) -> None:
        """Remove local copy of a file.
        
        Args:
            rel_path: Relative path to file
            file_name: File name for display
        """
        self._update_status(f"Removing local copy of {file_name}...")
        
        def remove_in_thread():
            try:
                local_path = validate_sync_path(rel_path, self.config.sync_directory)
                local_path.unlink(missing_ok=True)
                logger.info(f"Removed local copy: {rel_path}")
                
                cleanup_empty_parent_dirs(local_path, self.config.sync_directory)
                
                state = self.config.load_state()
                if 'files' in state and rel_path in state['files']:
                    state['files'][rel_path]['downloaded'] = False
                    self.config.save_state(state)
                
                GLib.idle_add(self._update_status, f"Removed local copy of {file_name}")
                GLib.idle_add(self._load_remote_files)
                
            except Exception as e:
                logger.error(f"Failed to remove local copy of {file_name}: {e}")
                GLib.idle_add(self._show_error, f"Failed to remove: {e}")
        
        thread = threading.Thread(target=remove_in_thread, daemon=True)
        thread.start()
    
    def _download_file(self, file_id: str, file_name: str) -> None:
        """Download file from OneDrive.
        
        Args:
            file_id: OneDrive file ID
            file_name: File name
        """
        self._update_status(f"Downloading {file_name}...")
        
        def download_in_thread():
            try:
                file_info = self.client.get_file_metadata(file_id)
                parent_path = file_info.get('parentReference', {}).get('path', '')
                if parent_path:
                    parent_path = sanitize_onedrive_path(parent_path)
                
                rel_path = str(Path(parent_path) / file_name) if parent_path else file_name
                local_path = validate_sync_path(rel_path, self.config.sync_directory)
                
                metadata = self.client.download_file(file_id, local_path)
                
                state = self.config.load_state()
                if 'files' not in state:
                    state['files'] = {}
                
                state['files'][rel_path] = {
                    'mtime': local_path.stat().st_mtime,
                    'size': file_info.get('size', 0),
                    'eTag': metadata.get('eTag', ''),
                    'remote_modified': metadata.get('lastModifiedDateTime', ''),
                    'downloaded': True,
                    'upload_error': None,
                }
                self.config.save_state(state)
                
                logger.info(f"Downloaded and marked for sync: {rel_path}")
                GLib.idle_add(self._update_status, f"Downloaded {file_name}")
                GLib.idle_add(self._load_remote_files)
            except Exception as e:
                error_msg = f"Failed to download {file_name}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                GLib.idle_add(self._show_error, "Download Failed", error_msg)
                GLib.idle_add(self._update_status, f"Download failed: {file_name}")
        
        thread = threading.Thread(target=download_in_thread, daemon=True)
        thread.start()
    
    def _download_files_batch(self, files: list) -> None:
        """Download multiple files in a single background thread.
        
        Args:
            files: List of tuples (file_id, file_name)
        """
        total = len(files)
        self._update_status(f"Downloading {total} files...")
        
        def download_batch():
            success_count = 0
            error_count = 0
            
            state = self.config.load_state()
            if 'files' not in state:
                state['files'] = {}
            
            for i, (file_id, file_name) in enumerate(files, 1):
                try:
                    GLib.idle_add(self._update_status, f"Downloading {i}/{total}: {file_name}")
                    
                    file_info = self.client.get_file_metadata(file_id)
                    parent_path = file_info.get('parentReference', {}).get('path', '')
                    if parent_path:
                        parent_path = sanitize_onedrive_path(parent_path)
                    
                    rel_path = str(Path(parent_path) / file_name) if parent_path else file_name
                    local_path = validate_sync_path(rel_path, self.config.sync_directory)
                    
                    metadata = self.client.download_file(file_id, local_path)
                    
                    state['files'][rel_path] = {
                        'mtime': local_path.stat().st_mtime,
                        'size': file_info.get('size', 0),
                        'eTag': metadata.get('eTag', ''),
                        'remote_modified': metadata.get('lastModifiedDateTime', ''),
                        'downloaded': True,
                        'upload_error': None,
                    }
                    
                    logger.info(f"Downloaded and marked for sync: {rel_path}")
                    success_count += 1
                    
                except Exception as e:
                    error_msg = f"Failed to download {file_name}: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    error_count += 1
            
            try:
                self.config.save_state(state)
                logger.info(f"Batch download complete: {success_count} succeeded, {error_count} failed")
            except Exception as e:
                logger.error(f"Failed to save state after batch download: {e}")
            
            if error_count > 0:
                GLib.idle_add(
                    self._update_status,
                    f"Downloaded {success_count}/{total} files ({error_count} failed)"
                )
                GLib.idle_add(
                    self._show_error,
                    "Download Incomplete",
                    f"Downloaded {success_count} files successfully.\n{error_count} files failed."
                )
            else:
                GLib.idle_add(self._update_status, f"Downloaded all {total} files successfully")
            
            GLib.idle_add(self._load_remote_files)
            GLib.idle_add(self._update_button_states)
        
        thread = threading.Thread(target=download_batch, daemon=True)
        thread.start()
