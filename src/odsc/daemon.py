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

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Raised when a security violation is detected."""
    pass


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
        
        if self.observer:
            self.observer.stop()
            self.observer.join()
        
        if self._sync_thread:
            self._sync_thread.join(timeout=5)
        
        logger.info("Sync daemon stopped")
    
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
        
        # Ensure 'files' key exists in state
        if 'files' not in self.state:
            self.state['files'] = {}
        if 'file_cache' not in self.state:
            self.state['file_cache'] = {}
        
        # Get delta token from state (None for initial sync)
        delta_token = self.state.get('delta_token')
        
        # Fetch changes using delta query (much faster than list_all_files)
        logger.info("Fetching changes from OneDrive using delta query...")
        try:
            if delta_token:
                # Incremental sync - only changed files
                changes, new_delta_token = self.client.get_delta(delta_token)
                logger.info(f"Incremental sync: {len(changes)} changes detected")
            else:
                # Initial sync - all files
                changes, new_delta_token = self.client.get_delta(None)
                logger.info(f"Initial sync: {len(changes)} total items")
            
            # Store new delta token for next sync
            self.state['delta_token'] = new_delta_token
            
        except Exception as e:
            logger.error(f"Failed to fetch changes: {e}", exc_info=True)
            # On error, fall back to full sync next time
            self.state['delta_token'] = None
            return
        
        # Process changes and update cache
        remote_files = {}
        for item in changes:
            # Check for deletions
            if item.get('deleted'):
                # Item was deleted on OneDrive
                item_id = item['id']
                # Find and remove from cache by ID
                for path, cached_item in list(self.state['file_cache'].items()):
                    if cached_item.get('id') == item_id:
                        logger.info(f"Item deleted on OneDrive: {path}")
                        # Handle deletion
                        if path in self.state.get('files', {}):
                            local_path = sync_dir / path
                            if local_path.exists():
                                self._move_to_recycle_bin(local_path, path)
                            del self.state['files'][path]
                        del self.state['file_cache'][path]
                        break
                continue
            
            # Skip folders for file processing
            if 'folder' in item:
                # Update folder in cache but don't process as file
                try:
                    parent_path = item.get('parentReference', {}).get('path', '')
                    name = item.get('name', '')
                    
                    # Skip special 'root' folder (OneDrive system folder)
                    if name == 'root' and not parent_path:
                        logger.debug("Skipping special 'root' system folder")
                        continue
                    
                    if parent_path:
                        safe_parent = self._sanitize_onedrive_path(parent_path)
                        full_path = str(Path(safe_parent) / name) if safe_parent else name
                    else:
                        full_path = name
                    
                    # Validate path
                    self._validate_sync_path(full_path, sync_dir)
                    
                    # Store full folder metadata (not just id and is_folder flag)
                    # This ensures GUI can display folder names properly
                    self.state['file_cache'][full_path] = item
                except (SecurityError, Exception) as e:
                    logger.warning(f"Skipping unsafe folder: {e}")
                continue
            
            # Process files
            try:
                # Extract and sanitize path
                parent_path = item.get('parentReference', {}).get('path', '')
                name = item.get('name', '')
                
                # Sanitize OneDrive path
                if parent_path:
                    safe_parent = self._sanitize_onedrive_path(parent_path)
                    full_path = str(Path(safe_parent) / name) if safe_parent else name
                else:
                    full_path = name
                
                # Validate it's within sync directory
                validated_path = self._validate_sync_path(full_path, sync_dir)
                
                # Update cache with latest metadata
                self.state['file_cache'][full_path] = {
                    'id': item['id'],
                    'size': item.get('size', 0),
                    'eTag': item.get('eTag', ''),
                    'lastModifiedDateTime': item.get('lastModifiedDateTime', ''),
                    'is_folder': False,
                }
                
                remote_files[full_path] = {
                    'id': item['id'],
                    'size': item.get('size', 0),
                    'eTag': item.get('eTag', ''),
                    'lastModifiedDateTime': item.get('lastModifiedDateTime', ''),
                }
            except SecurityError as e:
                logger.warning(f"Skipping unsafe remote path: {e}")
                continue
            except Exception as e:
                logger.warning(f"Error processing remote item: {e}")
                continue
        
        logger.info(f"Processed {len(remote_files)} remote files from delta")
        
        # Scan local directory for files
        local_files = {}
        local_folders = {}
        for path in sync_dir.rglob('*'):
            # Skip hidden files and directories
            if any(part.startswith('.') for part in path.parts):
                continue
            
            if path.is_file():
                try:
                    rel_path = str(path.relative_to(sync_dir))
                    local_files[rel_path] = {
                        'path': path,
                        'mtime': path.stat().st_mtime,
                        'size': path.stat().st_size,
                    }
                except (OSError, PermissionError) as e:
                    logger.warning(f"Cannot access {path}: {e}")
                    continue
            elif path.is_dir():
                try:
                    rel_path = str(path.relative_to(sync_dir))
                    local_folders[rel_path] = {
                        'path': path,
                    }
                except (OSError, PermissionError) as e:
                    logger.warning(f"Cannot access {path}: {e}")
                    continue
        
        logger.info(f"Found {len(local_files)} local files and {len(local_folders)} local folders")
        
        # Get all remote files from cache (exclude folders)
        all_remote_files = {}
        for path, cached in self.state['file_cache'].items():
            # Exclude both OneDrive folders and daemon-created folders
            if not ('folder' in cached or cached.get('is_folder', False)):
                all_remote_files[path] = cached
        
        logger.info(f"Total remote files in cache: {len(all_remote_files)}")
        
        # Process each file with robust conflict detection
        # Use cache for full remote file list, remote_files only for changed files
        all_paths = set(local_files.keys()) | set(all_remote_files.keys())
        
        for rel_path in all_paths:
            local_info = local_files.get(rel_path)
            # Check if file changed (in delta) or just exists (in cache)
            remote_info = remote_files.get(rel_path) or all_remote_files.get(rel_path)
            state_entry = self.state['files'].get(rel_path, {})
            
            try:
                action = self._determine_sync_action(rel_path, local_info, remote_info, state_entry)
                
                if action == 'upload':
                    logger.info(f"Uploading: {rel_path}")
                    try:
                        metadata = self.client.upload_file(local_info['path'], rel_path)
                        self.state['files'][rel_path] = {
                            'mtime': local_info['mtime'],
                            'size': local_info['size'],
                            'eTag': metadata.get('eTag', ''),
                            'remote_modified': metadata.get('lastModifiedDateTime', ''),
                            'downloaded': True,  # We created it locally, so mark as downloaded
                            'upload_error': None,  # Clear any previous error
                        }
                    except Exception as upload_err:
                        logger.error(f"Upload failed for {rel_path}: {upload_err}")
                        self.state['files'][rel_path] = {
                            'mtime': local_info['mtime'],
                            'size': local_info['size'],
                            'downloaded': True,
                            'upload_error': str(upload_err),
                        }
                    
                elif action == 'download':
                    logger.info(f"Downloading updated version: {rel_path}")
                    try:
                        # Validate path before download
                        local_path = self._validate_sync_path(rel_path, sync_dir)
                        metadata = self.client.download_file(remote_info['id'], local_path)
                        self.state['files'][rel_path] = {
                            'mtime': local_path.stat().st_mtime,
                            'size': remote_info['size'],
                            'eTag': remote_info['eTag'],
                            'remote_modified': remote_info['lastModifiedDateTime'],
                            'downloaded': True,
                            'upload_error': None,
                        }
                    except Exception as download_err:
                        logger.error(f"Download failed for {rel_path}: {download_err}")
                        # Don't update state on download failure to allow retry
                    
                elif action == 'recycle':
                    logger.warning(f"File deleted remotely, moving to recycle bin: {rel_path}")
                    # Validate path before deletion
                    local_path = self._validate_sync_path(rel_path, sync_dir)
                    self._move_to_recycle_bin(local_path, rel_path)
                    # Remove from state
                    if rel_path in self.state['files']:
                        del self.state['files'][rel_path]
                    
                elif action == 'conflict':
                    logger.warning(f"CONFLICT detected for {rel_path} - keeping both versions")
                    # Keep local version and download remote as .conflict file
                    conflict_rel = f"{rel_path}.conflict"
                    conflict_path = self._validate_sync_path(conflict_rel, sync_dir)
                    metadata = self.client.download_file(remote_info['id'], conflict_path)
                    logger.info(f"Saved remote version as: {conflict_path}")
                    
                elif action == 'skip':
                    logger.debug(f"Skipping (up to date): {rel_path}")
                    
            except Exception as e:
                logger.error(f"Failed to sync {rel_path}: {e}", exc_info=True)
        
        # Sync folders
        # Get all remote folders from cache
        all_remote_folders = {}
        for path, cached in self.state['file_cache'].items():
            # Check both OneDrive format ('folder' key) and daemon format ('is_folder' flag)
            if 'folder' in cached or cached.get('is_folder', False):
                all_remote_folders[path] = cached
        
        logger.info(f"Total remote folders in cache: {len(all_remote_folders)}")
        
        # Process folders - upload new local folders to OneDrive
        for folder_path, folder_info in local_folders.items():
            if folder_path not in all_remote_folders:
                # New local folder not on OneDrive
                try:
                    logger.info(f"Creating folder on OneDrive: {folder_path}")
                    metadata = self.client.create_folder(folder_path)
                    # Store full metadata (not just id and is_folder)
                    # This ensures GUI can display folder names properly
                    self.state['file_cache'][folder_path] = metadata
                    logger.info(f"Folder created on OneDrive: {folder_path}")
                except Exception as e:
                    logger.error(f"Failed to create folder {folder_path} on OneDrive: {e}")
        
        # Process folders - create missing local folders from OneDrive
        for folder_path, folder_info in all_remote_folders.items():
            if folder_path not in local_folders:
                # Remote folder not present locally
                try:
                    local_path = self._validate_sync_path(folder_path, sync_dir)
                    logger.info(f"Creating local folder: {folder_path}")
                    local_path.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Local folder created: {folder_path}")
                except Exception as e:
                    logger.error(f"Failed to create local folder {folder_path}: {e}")
        
        # Process folders - handle deletions
        for folder_path in local_folders:
            if folder_path not in all_remote_folders:
                # Local folder exists but not on OneDrive
                # This means it was deleted from OneDrive - delete locally
                try:
                    local_path = self._validate_sync_path(folder_path, sync_dir)
                    logger.info(f"Folder deleted from OneDrive, removing locally: {folder_path}")
                    # Move to recycle bin instead of permanent delete
                    self._move_to_recycle_bin(local_path, folder_path)
                except Exception as e:
                    logger.error(f"Failed to remove local folder {folder_path}: {e}")
        
        # Update sync time
        self.state['last_sync'] = datetime.now().isoformat()
        self.config.save_state(self.state)
        
        # Clean up old files from recycle bin
        self._cleanup_recycle_bin()
        
        logger.info("Periodic sync completed")
    
    def _move_to_recycle_bin(self, local_path: Path, rel_path: str) -> None:
        """Move file to system recycle bin/trash.
        
        Args:
            local_path: Full path to the local file
            rel_path: Relative path for logging
        """
        try:
            if local_path.exists():
                send2trash(str(local_path))
                logger.info(f"Moved to recycle bin: {rel_path}")
            else:
                logger.warning(f"File not found for recycling: {rel_path}")
        except Exception as e:
            logger.error(f"Failed to move {rel_path} to recycle bin: {e}")
            # Fallback to permanent deletion if trash fails
            try:
                local_path.unlink(missing_ok=True)
                logger.warning(f"Permanently deleted (trash failed): {rel_path}")
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
        
        Args:
            rel_path: Relative file path
            local_info: Local file info (or None if doesn't exist locally)
            remote_info: Remote file info (or None if doesn't exist remotely)
            state_entry: Last known sync state
            
        Returns:
            Action: 'upload', 'download', 'conflict', 'recycle', or 'skip'
        """
        # Case 1: File only exists locally (new local file)
        if local_info and not remote_info:
            if not state_entry:
                # Never synced before, upload it
                return 'upload'
            elif state_entry.get('eTag'):
                # Was synced before but now missing remotely (deleted remotely)
                logger.info(f"{rel_path} was deleted remotely, moving to recycle bin")
                return 'recycle'
            else:
                return 'upload'
        
        # Case 2: File only exists remotely (new remote file or deleted locally)
        if remote_info and not local_info:
            if not state_entry:
                # Never synced before - user must manually download
                logger.debug(f"{rel_path} is new on OneDrive, awaiting user download")
                return 'skip'
            elif state_entry.get('downloaded') and state_entry.get('mtime'):
                # Was downloaded before but now deleted locally (user deleted)
                logger.info(f"{rel_path} was deleted locally, keeping deleted")
                return 'skip'
            elif state_entry.get('eTag'):
                # Was synced/uploaded but deleted locally (user deleted)
                logger.info(f"{rel_path} was deleted locally, keeping deleted")
                return 'skip'
            else:
                # No clear state, skip (require manual download)
                return 'skip'
        
        # Case 3: File exists both locally and remotely
        if local_info and remote_info:
            # Check if we've synced this file before
            if not state_entry:
                # No sync state - this shouldn't happen in normal flow
                # Could be user manually added file that already exists remotely
                if local_info['size'] == remote_info['size']:
                    # Assume same, record state
                    logger.info(f"{rel_path} exists both places with same size, assuming synced")
                    return 'skip'
                else:
                    # Different sizes, potential conflict
                    return 'conflict'
            
            # Only sync if file was explicitly downloaded by user or uploaded by us
            if not state_entry.get('downloaded'):
                # User never downloaded this, skip syncing
                logger.debug(f"{rel_path} not marked as downloaded, skipping sync")
                return 'skip'
            
            # Check if local file changed since last sync
            local_changed = (
                state_entry.get('mtime', 0) != local_info['mtime'] or
                state_entry.get('size', 0) != local_info['size']
            )
            
            # Check if remote file changed since last sync
            remote_changed = (
                state_entry.get('eTag', '') != remote_info['eTag'] or
                state_entry.get('remote_modified', '') != remote_info['lastModifiedDateTime']
            )
            
            if local_changed and remote_changed:
                # Both changed - conflict!
                logger.warning(f"Both local and remote changed: {rel_path}")
                return 'conflict'
            elif local_changed:
                # Only local changed - upload
                return 'upload'
            elif remote_changed:
                # Only remote changed - download
                return 'download'
            else:
                # Neither changed
                return 'skip'
        
        return 'skip'
    
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
            self.state['files'][str(rel_path)] = {
                'mtime': path.stat().st_mtime,
                'size': path.stat().st_size,
                'eTag': metadata.get('eTag', ''),
                'remote_modified': metadata.get('lastModifiedDateTime', ''),
                'synced': True,
                'downloaded': True,
                'upload_error': None,  # Clear error on success
            }
            self.config.save_state(self.state)
            
            logger.info(f"Synced file: {rel_path}")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to sync {rel_path}: {error_msg}", exc_info=True)
            
            # Track failed upload in state
            if 'files' not in self.state:
                self.state['files'] = {}
            
            self.state['files'][str(rel_path)] = {
                'mtime': path.stat().st_mtime,
                'size': path.stat().st_size,
                'synced': False,
                'downloaded': True,
                'upload_error': error_msg,
            }
            self.config.save_state(self.state)


def main():
    """Main entry point for daemon."""
    config = Config()
    daemon = SyncDaemon(config)
    daemon.start()


if __name__ == '__main__':
    main()
