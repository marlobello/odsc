#!/usr/bin/env python3
"""Sync daemon for ODSC."""

import logging
import os
import time
import threading
from pathlib import Path
from typing import Dict, Any, Set, Optional
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from .config import Config
from .onedrive_client import OneDriveClient
from .logging_config import setup_logging

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
        
        # Load sync state
        self.state = self.config.load_state()
    
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
                
                # Periodic full sync check
                if self._should_do_periodic_sync():
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
    
    def _do_periodic_sync(self) -> None:
        """Perform periodic two-way sync of all files."""
        logger.info("Starting periodic two-way sync...")
        
        sync_dir = self.config.sync_directory
        
        # Ensure 'files' key exists in state
        if 'files' not in self.state:
            self.state['files'] = {}
        
        # Get all files from OneDrive
        logger.info("Fetching remote file list...")
        try:
            remote_items = self.client.list_all_files()
        except Exception as e:
            logger.error(f"Failed to fetch remote files: {e}", exc_info=True)
            return
        
        # Build map of remote files (excluding folders)
        remote_files = {}
        for item in remote_items:
            if 'folder' not in item:
                # Extract path
                parent_path = item.get('parentReference', {}).get('path', '').replace('/drive/root:', '')
                name = item.get('name', '')
                if parent_path:
                    full_path = parent_path.lstrip('/') + '/' + name
                else:
                    full_path = name
                
                remote_files[full_path] = {
                    'id': item['id'],
                    'size': item.get('size', 0),
                    'eTag': item.get('eTag', ''),
                    'lastModifiedDateTime': item.get('lastModifiedDateTime', ''),
                }
        
        logger.info(f"Found {len(remote_files)} remote files")
        
        # Scan local directory
        local_files = {}
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
        
        logger.info(f"Found {len(local_files)} local files")
        
        # Process each file with robust conflict detection
        all_paths = set(local_files.keys()) | set(remote_files.keys())
        
        for rel_path in all_paths:
            local_info = local_files.get(rel_path)
            remote_info = remote_files.get(rel_path)
            state_entry = self.state['files'].get(rel_path, {})
            
            try:
                action = self._determine_sync_action(rel_path, local_info, remote_info, state_entry)
                
                if action == 'upload':
                    logger.info(f"Uploading: {rel_path}")
                    metadata = self.client.upload_file(local_info['path'], rel_path)
                    self.state['files'][rel_path] = {
                        'mtime': local_info['mtime'],
                        'size': local_info['size'],
                        'eTag': metadata.get('eTag', ''),
                        'remote_modified': metadata.get('lastModifiedDateTime', ''),
                    }
                    
                elif action == 'download':
                    logger.info(f"Downloading: {rel_path}")
                    local_path = sync_dir / rel_path
                    metadata = self.client.download_file(remote_info['id'], local_path)
                    self.state['files'][rel_path] = {
                        'mtime': local_path.stat().st_mtime,
                        'size': remote_info['size'],
                        'eTag': remote_info['eTag'],
                        'remote_modified': remote_info['lastModifiedDateTime'],
                    }
                    
                elif action == 'conflict':
                    logger.warning(f"CONFLICT detected for {rel_path} - keeping both versions")
                    # Keep local version and download remote as .conflict file
                    conflict_path = sync_dir / f"{rel_path}.conflict"
                    metadata = self.client.download_file(remote_info['id'], conflict_path)
                    logger.info(f"Saved remote version as: {conflict_path}")
                    
                elif action == 'skip':
                    logger.debug(f"Skipping (up to date): {rel_path}")
                    
            except Exception as e:
                logger.error(f"Failed to sync {rel_path}: {e}", exc_info=True)
        
        # Update sync time
        self.state['last_sync'] = datetime.now().isoformat()
        self.config.save_state(self.state)
        
        logger.info("Periodic sync completed")
    
    def _determine_sync_action(self, rel_path: str, local_info: Optional[Dict], 
                               remote_info: Optional[Dict], state_entry: Dict) -> str:
        """Determine what sync action to take for a file.
        
        Args:
            rel_path: Relative file path
            local_info: Local file info (or None if doesn't exist locally)
            remote_info: Remote file info (or None if doesn't exist remotely)
            state_entry: Last known sync state
            
        Returns:
            Action: 'upload', 'download', 'conflict', or 'skip'
        """
        # Case 1: File only exists locally (new local file)
        if local_info and not remote_info:
            if not state_entry:
                # Never synced before, upload it
                return 'upload'
            elif state_entry.get('eTag'):
                # Was synced before but now missing remotely (deleted remotely)
                logger.info(f"{rel_path} was deleted remotely, keeping local")
                return 'skip'
            else:
                return 'upload'
        
        # Case 2: File only exists remotely (new remote file or deleted locally)
        if remote_info and not local_info:
            if not state_entry:
                # Never synced before, download it
                return 'download'
            elif state_entry.get('mtime'):
                # Was synced before but now deleted locally (user deleted)
                logger.info(f"{rel_path} was deleted locally, keeping deleted")
                return 'skip'
            else:
                return 'download'
        
        # Case 3: File exists both locally and remotely
        if local_info and remote_info:
            # Check if we've synced this file before
            if not state_entry:
                # No sync state - compare by size
                if local_info['size'] == remote_info['size']:
                    # Assume same, record state
                    return 'skip'
                else:
                    # Different sizes, potential conflict - prefer newer
                    return 'conflict'
            
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
            self.client.upload_file(path, str(rel_path))
            
            # Update state
            self.state['files'][str(rel_path)] = {
                'mtime': path.stat().st_mtime,
                'synced': True,
            }
            self.config.save_state(self.state)
            
            logger.info(f"Synced file: {rel_path}")
            
        except Exception as e:
            logger.error(f"Failed to sync {rel_path}: {e}", exc_info=True)


def main():
    """Main entry point for daemon."""
    config = Config()
    daemon = SyncDaemon(config)
    daemon.start()


if __name__ == '__main__':
    main()
