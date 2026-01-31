#!/usr/bin/env python3
"""Sync daemon for ODSC."""

import logging
import os
import time
import signal
import threading
from pathlib import Path
from typing import Dict, Any, Set, Optional
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from send2trash import send2trash

from .config import Config
from .onedrive_client import OneDriveClient
from .logging_config import setup_logging
from .path_utils import sanitize_onedrive_path, validate_sync_path, extract_item_path, SecurityError

# Try to import system tray (optional - may not be available in headless environments)
try:
    from .system_tray import SystemTrayIndicator
    SYSTEM_TRAY_AVAILABLE = True
except ImportError:
    SYSTEM_TRAY_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("System tray not available (missing dependencies)")

logger = logging.getLogger(__name__)


class SyncEventHandler(FileSystemEventHandler):
    """Handles file system events for syncing."""
    
    def __init__(self, daemon: 'SyncDaemon'):
        """Initialize event handler.
        
        Args:
            daemon: Parent sync daemon
        """
        self.daemon = daemon
        self.pending_changes: Set[Path] = set()
        self._lock = threading.Lock()
    
    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification event."""
        if not event.is_directory:
            self._queue_change(Path(event.src_path))
    
    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file creation event."""
        if not event.is_directory:
            self._queue_change(Path(event.src_path))
    
    def on_deleted(self, event: FileSystemEvent) -> None:
        """Handle file deletion event."""
        if not event.is_directory:
            self._queue_change(Path(event.src_path))
    
    def _queue_change(self, path: Path) -> None:
        """Queue a file change for processing.
        
        Args:
            path: Path to changed file
        """
        with self._lock:
            self.pending_changes.add(path)
        logger.debug(f"Queued change: {path}")
    
    def get_pending_changes(self) -> Set[Path]:
        """Get and clear pending changes.
        
        Returns:
            Set of changed file paths
        """
        with self._lock:
            changes = self.pending_changes.copy()
            self.pending_changes.clear()
        return changes


class SyncDaemon:
    """Background daemon for syncing files to OneDrive."""
    
    def __init__(self, config: Config):
        """Initialize sync daemon.
        
        Args:
            config: Configuration manager
        """
        self.config = config
        self.client: Optional[OneDriveClient] = None
        self.observer: Optional[Observer] = None
        self.event_handler: Optional[SyncEventHandler] = None
        self._running = False
        self._sync_thread: Optional[threading.Thread] = None
        self.system_tray: Optional['SystemTrayIndicator'] = None
        self._tray_thread: Optional[threading.Thread] = None
        
        # Load sync state
        self.state = self.config.load_state()
        
        # Setup signal handlers
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            self.stop()
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    
    def initialize(self) -> bool:
        """Initialize OneDrive client and authentication.
        
        Returns:
            True if initialization successful
        """
        # Setup logging
        setup_logging(level=self.config.log_level, log_file=self.config.log_path)
        logger.info("=== ODSC Daemon Starting ===")
        
        # client_id is optional - will use default if not configured
        client_id = self.config.client_id or None
        
        # Load existing token
        token_data = self.config.load_token()
        if not token_data:
            logger.error("Not authenticated. Please run authentication first.")
            return False
        
        self.client = OneDriveClient(client_id, token_data)
        logger.info("OneDrive client initialized")
        return True
    
    def start(self) -> None:
        """Start the sync daemon."""
        if not self.initialize():
            logger.error("Failed to initialize daemon")
            return
        
        self._running = True
        
        # Ensure sync directory exists
        sync_dir = self.config.sync_directory
        sync_dir.mkdir(parents=True, exist_ok=True)
        
        # Start system tray indicator if available
        if SYSTEM_TRAY_AVAILABLE and os.environ.get('DISPLAY'):
            try:
                self._start_system_tray()
            except Exception as e:
                logger.warning(f"Could not start system tray: {e}")
        
        # Set up file system monitoring
        self.event_handler = SyncEventHandler(self)
        self.observer = Observer()
        self.observer.schedule(self.event_handler, str(sync_dir), recursive=True)
        self.observer.start()
        
        # Start periodic sync thread
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()
        
        logger.info(f"Sync daemon started. Monitoring: {sync_dir}")
        
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
    
    def stop(self) -> None:
        """Stop the sync daemon."""
        logger.info("Stopping sync daemon...")
        self._running = False
        
        # Stop system tray
        if self.system_tray:
            try:
                self.system_tray.quit()
            except Exception as e:
                logger.debug(f"Error stopping system tray: {e}")
        
        if self.observer:
            self.observer.stop()
            self.observer.join()
        
        if self._sync_thread:
            self._sync_thread.join(timeout=5)
        
        logger.info("Sync daemon stopped")
    
    def _start_system_tray(self):
        """Start the system tray indicator in a separate thread."""
        def run_tray():
            try:
                self.system_tray = SystemTrayIndicator(daemon=self)
                self.system_tray.run()  # Blocking GTK main loop
            except Exception as e:
                logger.error(f"System tray error: {e}", exc_info=True)
        
        self._tray_thread = threading.Thread(target=run_tray, daemon=True)
        self._tray_thread.start()
        logger.info("System tray indicator started")
    
    def _sync_loop(self) -> None:
        """Main sync loop (runs periodically)."""
        while self._running:
            try:
                # Process any pending file changes
                if self.event_handler:
                    pending = self.event_handler.get_pending_changes()
                    for path in pending:
                        self._sync_file(path)
                
                # Check for force sync signal
                if self._check_force_sync_signal():
                    logger.info("Force sync triggered by user")
                    self._do_periodic_sync()
                # Periodic full sync check
                elif self._should_do_periodic_sync():
                    self._do_periodic_sync()
                
            except Exception as e:
                logger.error(f"Error in sync loop: {e}", exc_info=True)
            
            # Wait for sync interval
            time.sleep(self.config.sync_interval)
    
    
    def _should_do_periodic_sync(self) -> bool:
        """Check if periodic sync should run.
        
        Returns:
            True if periodic sync needed
        """
        last_sync = self.state.get('last_sync')
        if not last_sync:
            return True
        
        # Parse last sync time
        last_sync_dt = datetime.fromisoformat(last_sync)
        elapsed = (datetime.now() - last_sync_dt).total_seconds()
        
        return elapsed >= self.config.sync_interval
    
    def _check_force_sync_signal(self) -> bool:
        """Check if force sync signal file exists.
        
        Returns:
            True if force sync requested
        """
        force_sync_path = self.config.force_sync_path
        if force_sync_path.exists():
            try:
                # Remove signal file
                force_sync_path.unlink()
                return True
            except Exception as e:
                logger.warning(f"Failed to remove force sync signal: {e}")
                return False
        return False
    
    def _do_periodic_sync(self) -> None:
        """Perform periodic two-way sync of all files using delta query."""
        logger.info("Starting periodic two-way sync...")
        
        sync_dir = self.config.sync_directory
        
        # Reload state to pick up any GUI changes
        self.state = self.config.load_state()
        
        # Initialize state
        self._ensure_state_initialized()
        
        # Track items deleted from OneDrive in this sync cycle
        # This prevents re-uploading them if local deletion failed
        self._deleted_from_remote = set()
        
        # Fetch and process changes from OneDrive
        remote_files = self._fetch_and_process_remote_changes(sync_dir)
        if remote_files is None:
            return  # Error occurred, abort sync
        
        # IMPORTANT: Verify deletions completed before scanning filesystem
        # This ensures deleted folders don't appear in the scan and get re-uploaded
        self._verify_and_retry_deletions(sync_dir)
        
        # Scan local filesystem
        local_files, local_folders = self._scan_local_filesystem(sync_dir)
        
        # Get all remote folders from cache
        all_remote_folders = self._get_all_remote_folders()
        
        # IMPORTANT: Sync folders FIRST before files
        # This ensures parent folders exist before uploading files
        self._sync_folders(sync_dir, local_folders, all_remote_folders)
        
        # Now sync files (folders already exist)
        all_remote_files = self._get_all_remote_files()
        self._sync_files(sync_dir, local_files, remote_files, all_remote_files)
        
        # Finalize sync
        self._finalize_sync()
        
        logger.info("Periodic sync completed")
    
    def _ensure_state_initialized(self) -> None:
        """Ensure state dictionaries are initialized."""
        if 'files' not in self.state:
            self.state['files'] = {}
        if 'file_cache' not in self.state:
            self.state['file_cache'] = {}
    
    def _fetch_and_process_remote_changes(self, sync_dir: Path) -> Optional[Dict[str, Any]]:
        """Fetch changes from OneDrive and process them.
        
        Returns:
            Dictionary of remote files, or None if error occurred
        """
        delta_token = self.state.get('delta_token')
        
        # Fetch changes using delta query
        logger.info("Fetching changes from OneDrive using delta query...")
        try:
            if delta_token:
                changes, new_delta_token = self.client.get_delta(delta_token)
                logger.info(f"Incremental sync: {len(changes)} changes detected")
            else:
                changes, new_delta_token = self.client.get_delta(None)
                logger.info(f"Initial sync: {len(changes)} total items")
            
            self.state['delta_token'] = new_delta_token
            
        except Exception as e:
            logger.error(f"Failed to fetch changes: {e}", exc_info=True)
            self.state['delta_token'] = None
            return None
        
        # Process changes
        remote_files = {}
        for item in changes:
            # Skip the drive root itself
            if 'root' in item or item.get('name') == 'root':
                logger.debug(f"Skipping drive root object")
                continue
            
            if item.get('deleted'):
                self._process_remote_deletion(item)
            elif 'folder' in item:
                self._process_remote_folder(item, sync_dir)
            else:
                file_info = self._process_remote_file(item, sync_dir)
                if file_info:
                    remote_files[file_info['path']] = file_info['metadata']
        
        logger.info(f"Processed {len(remote_files)} remote files from delta")
        return remote_files
    
    def _process_remote_deletion(self, item: Dict[str, Any]) -> None:
        """Process a deleted item from OneDrive (file or folder)."""
        item_id = item['id']
        for path, cached_item in list(self.state['file_cache'].items()):
            if cached_item.get('id') == item_id:
                is_folder = 'folder' in cached_item or cached_item.get('is_folder', False)
                item_type = "folder" if is_folder else "file"
                logger.info(f"{item_type.capitalize()} deleted on OneDrive: {path}")
                
                # Track this deletion to prevent re-upload
                if hasattr(self, '_deleted_from_remote'):
                    self._deleted_from_remote.add(path)
                    logger.debug(f"Added {path} to deleted tracking set")
                
                # Delete local file/folder if it exists
                local_path = self.config.sync_directory / path
                if local_path.exists():
                    self._move_to_recycle_bin(local_path, path)
                else:
                    logger.debug(f"Local path doesn't exist (may have been deleted already): {path}")
                
                # Remove from cache and state
                self._remove_from_cache(path)
                break
    
    def _verify_and_retry_deletions(self, sync_dir: Path) -> None:
        """Verify deletions completed and retry if necessary.
        
        This ensures folders deleted from OneDrive are actually removed locally
        before we scan the filesystem. Otherwise they might be re-uploaded.
        
        Args:
            sync_dir: Sync directory path
        """
        if not hasattr(self, '_deleted_from_remote') or not self._deleted_from_remote:
            return  # Nothing to verify
        
        import shutil
        
        for path in list(self._deleted_from_remote):
            local_path = sync_dir / path
            
            # Validate path is within sync directory (protect against symlink attacks)
            try:
                validate_sync_path(sync_dir, local_path)
            except SecurityError as e:
                logger.error(f"Path validation failed for deletion: {path} - {e}")
                continue
            
            # Check if deletion succeeded (use try/except to avoid TOCTOU)
            try:
                # Try to stat the file - if it doesn't exist, this will raise FileNotFoundError
                local_path.stat()
            except FileNotFoundError:
                logger.debug(f"Deletion verified successful: {path}")
                continue
            
            # Deletion failed or incomplete - retry with more aggressive approach
            logger.warning(f"Deletion incomplete, retrying: {path}")
            
            for attempt in range(3):
                try:
                    if local_path.is_dir():
                        # Re-validate before rmtree to prevent symlink attacks
                        validate_sync_path(sync_dir, local_path)
                        # For directories, use rmtree directly (skip send2trash)
                        shutil.rmtree(local_path, ignore_errors=False)
                        logger.info(f"Directory deleted on retry {attempt + 1}: {path}")
                    else:
                        # For files, use unlink with try/except (avoid TOCTOU)
                        try:
                            local_path.unlink()
                            logger.info(f"File deleted on retry {attempt + 1}: {path}")
                        except FileNotFoundError:
                            logger.debug(f"File already gone during retry: {path}")
                    
                    # Verify it's really gone
                    try:
                        local_path.stat()
                        # Still exists, continue retrying
                    except FileNotFoundError:
                        # Successfully deleted
                        break
                    
                except PermissionError as e:
                    if attempt < 2:
                        # Wait briefly and retry (file might be locked)
                        logger.debug(f"Permission denied, will retry: {e}")
                        time.sleep(0.5)
                    else:
                        logger.error(f"Permission denied after 3 attempts: {path}")
                        logger.error(f"Folder will remain locally but won't be uploaded (tracked in _deleted_from_remote)")
                except Exception as e:
                    if attempt < 2:
                        logger.debug(f"Deletion failed, will retry: {e}")
                        time.sleep(0.5)
                    else:
                        logger.error(f"Could not delete after 3 attempts: {path} - {e}")
                        logger.error(f"Item will remain locally but won't be uploaded (tracked in _deleted_from_remote)")
    
    def _process_remote_folder(self, item: Dict[str, Any], sync_dir: Path) -> None:
        """Process a folder from OneDrive delta."""
        try:
            # Skip the drive root itself
            if 'root' in item or item.get('name') == 'root':
                logger.debug("Skipping drive root object")
                return
            
            full_path = extract_item_path(item)
            validate_sync_path(full_path, sync_dir)
            self.state['file_cache'][full_path] = item
        except (SecurityError, Exception) as e:
            logger.warning(f"Skipping unsafe folder: {e}")
    
    def _process_remote_file(self, item: Dict[str, Any], sync_dir: Path) -> Optional[Dict[str, Any]]:
        """Process a file from OneDrive delta.
        
        Returns:
            Dict with 'path' and 'metadata' keys, or None if error
        """
        try:
            full_path = extract_item_path(item)
            validate_sync_path(full_path, sync_dir)
            
            metadata = {
                'id': item['id'],
                'size': item.get('size', 0),
                'eTag': item.get('eTag', ''),
                'lastModifiedDateTime': item.get('lastModifiedDateTime', ''),
            }
            
            # Update cache
            self.state['file_cache'][full_path] = {**metadata, 'is_folder': False}
            
            return {'path': full_path, 'metadata': metadata}
            
        except SecurityError as e:
            logger.warning(f"Skipping unsafe remote path: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error processing remote item: {e}")
            return None
    
    def _scan_local_filesystem(self, sync_dir: Path) -> tuple:
        """Scan local filesystem for files and folders.
        
        Returns:
            Tuple of (local_files dict, local_folders dict)
        """
        local_files = {}
        local_folders = {}
        
        for path in sync_dir.rglob('*'):
            # Skip hidden files and directories
            if any(part.startswith('.') for part in path.parts):
                continue
            
            try:
                rel_path = str(path.relative_to(sync_dir))
                
                # Cache stat result to avoid TOCTOU race conditions
                stat_info = path.stat()
                
                if path.is_file():
                    local_files[rel_path] = {
                        'path': path,
                        'mtime': stat_info.st_mtime,
                        'size': stat_info.st_size,
                    }
                elif path.is_dir():
                    local_folders[rel_path] = {'path': path}
                    
            except (OSError, PermissionError) as e:
                logger.warning(f"Cannot access {path}: {e}")
        
        logger.info(f"Found {len(local_files)} local files and {len(local_folders)} local folders")
        return local_files, local_folders
    
    def _get_all_remote_files(self) -> Dict[str, Any]:
        """Get all remote files from cache (excluding folders)."""
        all_remote_files = {}
        for path, cached in self.state['file_cache'].items():
            if not ('folder' in cached or cached.get('is_folder', False)):
                all_remote_files[path] = cached
        
        logger.info(f"Total remote files in cache: {len(all_remote_files)}")
        return all_remote_files
    
    def _get_all_remote_folders(self) -> Dict[str, Any]:
        """Get all remote folders from cache."""
        all_remote_folders = {}
        for path, cached in self.state['file_cache'].items():
            # Skip the drive root itself
            if 'root' in cached or path == 'root':
                continue
            
            if 'folder' in cached or cached.get('is_folder', False):
                all_remote_folders[path] = cached
        
        logger.info(f"Total remote folders in cache: {len(all_remote_folders)}")
        return all_remote_folders
    
    def _sync_files(self, sync_dir: Path, local_files: Dict, remote_files: Dict, all_remote_files: Dict) -> None:
        """Sync files between local and remote (optimized)."""
        import time
        start_time = time.time()
        
        # OPTIMIZATION: Only process files that actually need syncing
        # Instead of iterating through ALL 25K+ remote files, we:
        # 1. Check all local files (small set - ~80 files)
        # 2. Check only files changed remotely in this delta (small set)
        # 3. Skip everything else (already in sync)
        
        processed = set()
        sync_count = {'upload': 0, 'download': 0, 'skip': 0, 'conflict': 0, 'recycle': 0}
        
        # Process local files (check if they need upload)
        for rel_path, local_info in local_files.items():
            remote_info = remote_files.get(rel_path) or all_remote_files.get(rel_path)
            state_entry = self.state['files'].get(rel_path, {})
            
            try:
                action = self._determine_sync_action(rel_path, local_info, remote_info, state_entry)
                self._execute_sync_action(action, rel_path, sync_dir, local_info, remote_info)
                sync_count[action] = sync_count.get(action, 0) + 1
                processed.add(rel_path)
            except Exception as e:
                logger.error(f"Failed to sync {rel_path}: {e}", exc_info=True)
        
        # Process files changed remotely (check if they need download)
        # remote_files contains ONLY files that changed in this delta query
        for rel_path, remote_info in remote_files.items():
            if rel_path in processed:
                continue  # Already processed above
            
            local_info = local_files.get(rel_path)
            state_entry = self.state['files'].get(rel_path, {})
            
            try:
                action = self._determine_sync_action(rel_path, local_info, remote_info, state_entry)
                self._execute_sync_action(action, rel_path, sync_dir, local_info, remote_info)
                sync_count[action] = sync_count.get(action, 0) + 1
                processed.add(rel_path)
            except Exception as e:
                logger.error(f"Failed to sync {rel_path}: {e}", exc_info=True)
        
        elapsed = time.time() - start_time
        logger.info(f"File sync completed in {elapsed:.2f}s: "
                   f"{sync_count['upload']} uploaded, {sync_count['download']} downloaded, "
                   f"{sync_count['skip']} skipped, {sync_count['conflict']} conflicts, "
                   f"{sync_count['recycle']} recycled ({len(processed)} total processed)")
    
    def _execute_sync_action(self, action: str, rel_path: str, sync_dir: Path, 
                            local_info: Optional[Dict], remote_info: Optional[Dict]) -> None:
        """Execute a determined sync action."""
        if action == 'upload':
            self._upload_file(rel_path, local_info)
        elif action == 'download':
            self._download_file(rel_path, sync_dir, remote_info)
        elif action == 'recycle':
            self._recycle_remote_deleted_file(rel_path, sync_dir)
        elif action == 'conflict':
            self._handle_file_conflict(rel_path, sync_dir, remote_info)
        elif action == 'skip':
            logger.debug(f"Skipping (up to date): {rel_path}")
    
    def _update_file_state(self, rel_path: str, mtime: float, size: int, 
                          metadata: Optional[Dict] = None, error: Optional[str] = None) -> None:
        """Update file state in sync tracking.
        
        Args:
            rel_path: Relative file path
            mtime: File modification time
            size: File size
            metadata: OneDrive metadata (eTag, lastModifiedDateTime) if successful
            error: Error message if sync failed
        """
        state_entry = {
            'mtime': mtime,
            'size': size,
            'downloaded': True,
        }
        
        if error:
            state_entry['upload_error'] = error
        else:
            state_entry['eTag'] = metadata.get('eTag', '') if metadata else ''
            state_entry['remote_modified'] = metadata.get('lastModifiedDateTime', '') if metadata else ''
            state_entry['upload_error'] = None
        
        self.state['files'][rel_path] = state_entry
    
    def _remove_from_cache(self, path: str) -> None:
        """Remove item from cache and file state.
        
        Args:
            path: Relative path to remove
        """
        if path in self.state.get('files', {}):
            del self.state['files'][path]
        if path in self.state.get('file_cache', {}):
            del self.state['file_cache'][path]
            logger.debug(f"Removed {path} from cache")
    
    def _upload_file(self, rel_path: str, local_info: Dict) -> None:
        """Upload a local file to OneDrive."""
        logger.info(f"Uploading: {rel_path}")
        try:
            metadata = self.client.upload_file(local_info['path'], rel_path)
            self._update_file_state(rel_path, local_info['mtime'], local_info['size'], metadata)
        except Exception as upload_err:
            logger.error(f"Upload failed for {rel_path}: {upload_err}")
            self._update_file_state(rel_path, local_info['mtime'], local_info['size'], error=str(upload_err))
    
    def _download_file(self, rel_path: str, sync_dir: Path, remote_info: Dict) -> None:
        """Download a file from OneDrive."""
        logger.info(f"Downloading updated version: {rel_path}")
        try:
            local_path = validate_sync_path(rel_path, sync_dir)
            metadata = self.client.download_file(remote_info['id'], local_path)
            self._update_file_state(rel_path, local_path.stat().st_mtime, remote_info['size'], remote_info)
        except Exception as download_err:
            logger.error(f"Download failed for {rel_path}: {download_err}")
    
    def _recycle_remote_deleted_file(self, rel_path: str, sync_dir: Path) -> None:
        """Handle a file that was deleted remotely."""
        logger.warning(f"File deleted remotely, moving to recycle bin: {rel_path}")
        local_path = validate_sync_path(rel_path, sync_dir)
        self._move_to_recycle_bin(local_path, rel_path)
        self._remove_from_cache(rel_path)
    
    def _handle_file_conflict(self, rel_path: str, sync_dir: Path, remote_info: Dict) -> None:
        """Handle a file conflict by keeping both versions."""
        logger.warning(f"CONFLICT detected for {rel_path} - keeping both versions")
        conflict_rel = f"{rel_path}.conflict"
        conflict_path = validate_sync_path(conflict_rel, sync_dir)
        metadata = self.client.download_file(remote_info['id'], conflict_path)
        logger.info(f"Saved remote version as: {conflict_path}")
    
    def _sync_folders(self, sync_dir: Path, local_folders: Dict, all_remote_folders: Dict) -> None:
        """Sync folders between local and remote."""
        # Handle deletions first
        self._delete_folders_removed_from_remote(sync_dir, local_folders, all_remote_folders)
        
        # Upload new local folders to OneDrive
        self._upload_new_local_folders(local_folders, all_remote_folders)
        
        # Create missing local folders from OneDrive
        self._create_missing_local_folders(sync_dir, local_folders, all_remote_folders)
    
    def _delete_folders_removed_from_remote(self, sync_dir: Path, local_folders: Dict, 
                                           all_remote_folders: Dict) -> None:
        """Delete local folders that were removed from OneDrive.
        
        This is a safety net for folders that were deleted from OneDrive in 
        PREVIOUS sync cycles where deletion failed or was incomplete.
        
        For current-sync deletions, _verify_and_retry_deletions() handles them.
        This method catches historical edge cases where:
        1. Folder was deleted from OneDrive in a previous sync
        2. Local deletion failed at that time
        3. Folder is still in cache (wasn't removed for some reason)
        4. Daemon was restarted (lost _deleted_from_remote tracking)
        
        Only deletes folders that:
        1. Exist locally
        2. Are in cache (were synced before)
        3. No longer exist on OneDrive (deleted remotely)
        """
        folders_to_delete = []
        
        for folder_path in local_folders:
            # Check if folder was previously in cache (was synced before)
            if folder_path in self.state.get('file_cache', {}):
                # Folder was synced before, check if it still exists on OneDrive
                if folder_path not in all_remote_folders:
                    # Folder was deleted from OneDrive
                    folders_to_delete.append(folder_path)
        
        for folder_path in folders_to_delete:
            try:
                local_path = validate_sync_path(folder_path, sync_dir)
                logger.info(f"Folder deleted from OneDrive, removing locally: {folder_path}")
                self._move_to_recycle_bin(local_path, folder_path)
                
                del local_folders[folder_path]
                self._remove_from_cache(folder_path)
            except Exception as e:
                logger.error(f"Failed to remove local folder {folder_path}: {e}")
    
    def _upload_new_local_folders(self, local_folders: Dict, all_remote_folders: Dict) -> None:
        """Upload new local folders to OneDrive.
        
        Skips folders that were deleted from OneDrive in this sync cycle.
        """
        for folder_path in local_folders:
            if folder_path not in all_remote_folders:
                # Check if this folder was just deleted from OneDrive
                if hasattr(self, '_deleted_from_remote') and folder_path in self._deleted_from_remote:
                    logger.info(f"Skipping upload of {folder_path} - was deleted from OneDrive in this sync")
                    continue
                
                try:
                    logger.info(f"Creating folder on OneDrive: {folder_path}")
                    metadata = self.client.create_folder(folder_path)
                    self.state['file_cache'][folder_path] = metadata
                    logger.info(f"Folder created on OneDrive: {folder_path}")
                except Exception as e:
                    logger.error(f"Failed to create folder {folder_path} on OneDrive: {e}")
    
    def _create_missing_local_folders(self, sync_dir: Path, local_folders: Dict, 
                                     all_remote_folders: Dict) -> None:
        """Create local folders that exist on OneDrive but not locally."""
        for folder_path in all_remote_folders:
            if folder_path not in local_folders:
                try:
                    local_path = validate_sync_path(folder_path, sync_dir)
                    logger.info(f"Creating local folder: {folder_path}")
                    local_path.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Local folder created: {folder_path}")
                except Exception as e:
                    logger.error(f"Failed to create local folder {folder_path}: {e}")
    
    def _finalize_sync(self) -> None:
        """Finalize sync by updating state and cleaning up."""
        self.state['last_sync'] = datetime.now().isoformat()
        self.config.save_state(self.state)
        self._cleanup_recycle_bin()
    
    def _move_to_recycle_bin(self, local_path: Path, rel_path: str) -> None:
        """Move file or folder to system recycle bin/trash.
        
        Args:
            local_path: Full path to the local file or folder
            rel_path: Relative path for logging
        """
        try:
            if local_path.exists():
                send2trash(str(local_path))
                item_type = "folder" if local_path.is_dir() else "file"
                logger.info(f"Moved {item_type} to recycle bin: {rel_path}")
            else:
                logger.warning(f"Item not found for recycling: {rel_path}")
        except Exception as e:
            logger.error(f"Failed to move {rel_path} to recycle bin: {e}")
            # Fallback to permanent deletion if trash fails
            try:
                if local_path.is_dir():
                    # For directories, use rmtree
                    import shutil
                    shutil.rmtree(local_path, ignore_errors=False)
                    logger.warning(f"Permanently deleted folder (trash failed): {rel_path}")
                else:
                    # For files, use unlink
                    local_path.unlink(missing_ok=True)
                    logger.warning(f"Permanently deleted file (trash failed): {rel_path}")
            except Exception as e2:
                logger.error(f"Failed to delete {rel_path}: {e2}")
    
    def _cleanup_recycle_bin(self) -> None:
        """Clean up old recycle bin entries.
        
        Note: send2trash handles cleanup automatically through the OS.
        This method is kept for future custom cleanup logic if needed.
        """
        # OS handles recycle bin cleanup automatically
        # Could add custom logic here for tracking recycled files
        pass
    
    def _determine_sync_action(self, rel_path: str, local_info: Optional[Dict], 
                               remote_info: Optional[Dict], state_entry: Dict) -> str:
        """Determine what sync action to take for a file.
        
        Orchestrates sync decision by delegating to scenario-specific handlers.
        
        Args:
            rel_path: Relative file path
            local_info: Local file info (or None if doesn't exist locally)
            remote_info: Remote file info (or None if doesn't exist remotely)
            state_entry: Last known sync state
            
        Returns:
            Action: 'upload', 'download', 'conflict', 'recycle', or 'skip'
        """
        # Check if file was deleted remotely in this sync cycle
        if hasattr(self, '_deleted_from_remote') and rel_path in self._deleted_from_remote:
            logger.info(f"{rel_path} was deleted from OneDrive in this sync, moving to recycle bin")
            return 'recycle'
        
        # Delegate to scenario-specific handlers
        if self._is_local_only(local_info, remote_info):
            return self._handle_local_only_file(rel_path, state_entry)
        
        if self._is_remote_only(local_info, remote_info):
            return self._handle_remote_only_file(rel_path, state_entry)
        
        if self._exists_both_places(local_info, remote_info):
            return self._handle_file_exists_both(rel_path, local_info, remote_info, state_entry)
        
        return 'skip'
    
    def _is_local_only(self, local_info: Optional[Dict], remote_info: Optional[Dict]) -> bool:
        """Check if file exists only locally."""
        return local_info is not None and remote_info is None
    
    def _is_remote_only(self, local_info: Optional[Dict], remote_info: Optional[Dict]) -> bool:
        """Check if file exists only remotely."""
        return remote_info is not None and local_info is None
    
    def _exists_both_places(self, local_info: Optional[Dict], remote_info: Optional[Dict]) -> bool:
        """Check if file exists both locally and remotely."""
        return local_info is not None and remote_info is not None
    
    def _handle_local_only_file(self, rel_path: str, state_entry: Dict) -> str:
        """Handle file that only exists locally.
        
        Returns:
            'upload' if new file, 'recycle' if was deleted remotely
        """
        if not state_entry:
            # Check if file is in cache (was on OneDrive before deletion)
            if rel_path in self.state.get('file_cache', {}):
                logger.info(f"{rel_path} was deleted remotely (found in cache), moving to recycle bin")
                return 'recycle'
            # Never synced before, upload it
            return 'upload'
        
        if state_entry.get('eTag'):
            # Was synced before but now missing remotely (deleted remotely)
            logger.info(f"{rel_path} was deleted remotely, moving to recycle bin")
            return 'recycle'
        
        return 'upload'
    
    def _handle_remote_only_file(self, rel_path: str, state_entry: Dict) -> str:
        """Handle file that only exists remotely.
        
        Returns:
            'skip' - either new remote file (needs manual download) or deleted locally (respect deletion)
        """
        if not state_entry:
            # Never synced before - user must manually download
            logger.debug(f"{rel_path} is new on OneDrive, awaiting user download")
            return 'skip'
        
        if state_entry.get('downloaded') or state_entry.get('eTag'):
            # Was synced before but now deleted locally (user deleted)
            logger.info(f"{rel_path} was deleted locally, keeping deleted")
            return 'skip'
        
        # No clear state, skip (require manual download)
        return 'skip'
    
    def _handle_file_exists_both(self, rel_path: str, local_info: Dict, 
                                 remote_info: Dict, state_entry: Dict) -> str:
        """Handle file that exists both locally and remotely.
        
        Returns:
            'upload', 'download', 'conflict', or 'skip'
        """
        if not state_entry:
            # No sync state - handle gracefully
            return self._handle_untracked_file(rel_path, local_info, remote_info)
        
        # Only sync if file was explicitly downloaded by user or uploaded by us
        if not state_entry.get('downloaded'):
            logger.debug(f"{rel_path} not marked as downloaded, skipping sync")
            return 'skip'
        
        # Determine what changed
        local_changed = self._is_local_modified(local_info, state_entry)
        remote_changed = self._is_remote_modified(remote_info, state_entry)
        
        # Decide action based on what changed
        if local_changed and remote_changed:
            logger.warning(f"Both local and remote changed: {rel_path}")
            return 'conflict'
        elif local_changed:
            return 'upload'
        elif remote_changed:
            return 'download'
        else:
            return 'skip'
    
    def _handle_untracked_file(self, rel_path: str, local_info: Dict, remote_info: Dict) -> str:
        """Handle file that exists both places but has no sync state.
        
        Could happen if user manually added file that already exists remotely.
        
        Returns:
            'skip' if same size, 'conflict' if different
        """
        if local_info['size'] == remote_info['size']:
            logger.info(f"{rel_path} exists both places with same size, assuming synced")
            return 'skip'
        else:
            return 'conflict'
    
    def _is_local_modified(self, local_info: Dict, state_entry: Dict) -> bool:
        """Check if local file has been modified since last sync."""
        return (state_entry.get('mtime', 0) != local_info['mtime'] or
                state_entry.get('size', 0) != local_info['size'])
    
    def _is_remote_modified(self, remote_info: Dict, state_entry: Dict) -> bool:
        """Check if remote file has been modified since last sync."""
        return (state_entry.get('eTag', '') != remote_info['eTag'] or
                state_entry.get('remote_modified', '') != remote_info['lastModifiedDateTime'])
    
    def _sync_file(self, path: Path) -> None:
        """Sync a single file to OneDrive.
        
        Args:
            path: File path to sync
        """
        sync_dir = self.config.sync_directory
        
        # Check if file is within sync directory
        try:
            rel_path = path.relative_to(sync_dir)
        except ValueError:
            logger.debug(f"File not in sync directory: {path}")
            return
        
        if not path.exists():
            # File was deleted - we don't auto-delete from OneDrive
            logger.info(f"File deleted locally (not deleting from OneDrive): {rel_path}")
            return
        
        try:
            # Upload file
            metadata = self.client.upload_file(path, str(rel_path))
            
            # Update state - clear any previous error
            self._update_file_state(str(rel_path), path.stat().st_mtime, path.stat().st_size, metadata)
            self.config.save_state(self.state)
            
            logger.info(f"Synced file: {rel_path}")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to sync {rel_path}: {error_msg}", exc_info=True)
            
            # Track failed upload in state
            if 'files' not in self.state:
                self.state['files'] = {}
            
            self._update_file_state(str(rel_path), path.stat().st_mtime, path.stat().st_size, error=error_msg)
            self.config.save_state(self.state)


def main():
    """Main entry point for daemon."""
    config = Config()
    daemon = SyncDaemon(config)
    daemon.start()


if __name__ == '__main__':
    main()
