#!/usr/bin/env python3
"""Sync daemon for ODSC."""

import json
import logging
import os
import time
import signal
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any, Set, Optional, List
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from send2trash import send2trash

from . import __version__
from .config import Config
from .error_handling import log_exception, get_http_status
from .onedrive_client import OneDriveClient
from .logging_config import setup_logging
from .path_utils import sanitize_onedrive_path, validate_sync_path, extract_item_path, SecurityError
from .quickxorhash import extract_quickxorhash, quickxorhash_file
from .command_socket import CommandServer
from .sync_state import SyncStateManager
from .sync import SyncDecisionEngine

GITHUB_RELEASES_API = "https://api.github.com/repos/marlobello/odsc/releases/latest"
UPDATE_CHECK_INTERVAL = 86400  # 24 hours

# Try to import system tray (optional - may not be available in headless environments)
try:
    from .system_tray import SystemTrayIndicator
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib
    SYSTEM_TRAY_AVAILABLE = True
except (ImportError, ValueError):
    SYSTEM_TRAY_AVAILABLE = False
    Gtk = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]

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
        self.pending_moves: Dict[Path, tuple] = {}  # src → (dst, is_directory)
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

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handle file or directory rename/move event."""
        src = Path(event.src_path)
        dst = Path(event.dest_path)
        with self._lock:
            self.pending_moves[src] = (dst, event.is_directory)
        logger.debug(f"Queued move: {src} → {dst}")
        self.daemon._wakeup_event.set()
    
    def _queue_change(self, path: Path) -> None:
        """Queue a file change for processing.
        
        Args:
            path: Path to changed file
        """
        with self._lock:
            self.pending_changes.add(path)
        logger.debug(f"Queued change: {path}")
        self.daemon._wakeup_event.set()
    
    def get_pending_changes(self) -> Set[Path]:
        """Get and clear pending changes.
        
        Returns:
            Set of changed file paths
        """
        with self._lock:
            changes = self.pending_changes.copy()
            self.pending_changes.clear()
        return changes

    def get_pending_moves(self) -> Dict[Path, tuple]:
        """Get and clear pending move/rename events.

        Returns:
            Dict mapping src Path → (dst Path, is_directory)
        """
        with self._lock:
            moves = self.pending_moves.copy()
            self.pending_moves.clear()
        return moves


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
        self._stopping = False
        self._gtk_mode = False
        self._sync_thread: Optional[threading.Thread] = None
        self.system_tray: Optional['SystemTrayIndicator'] = None
        self._wakeup_event = threading.Event()
        # Set when an immediate full sync is requested (e.g. via the command
        # socket SYNC command), as opposed to merely waking the loop to process
        # pending watchdog changes.
        self._force_sync_requested = threading.Event()
        self._last_update_check: float = 0.0
        self._command_server: Optional[CommandServer] = None

        self.state_mgr = SyncStateManager(
            self.config.load_state, self.config.save_state, self.config.persist_sync_entry
        )
        self.state_mgr.load()
        self.decision_engine = SyncDecisionEngine(self.state_mgr.get_cache_entry)
        
        # Setup signal handlers (Python-level, used during startup and headless mode)
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            self.stop()
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    def _log_operation_error(self, message: str, exc: BaseException, *, exc_info: bool = False) -> None:
        """Log sync/runtime errors with warning for transient failures."""
        log_exception(logger, message, exc, exc_info=exc_info)
    
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
        
        # Load existing token, retrying to handle the keyring being temporarily
        # unavailable at login (e.g. GNOME Keyring not yet unlocked).
        token_data = None
        if self.config.token_path.exists():
            for attempt in range(1, 6):
                token_data = self.config.load_token()
                if token_data is not None:
                    break
                logger.info(
                    f"Keyring not yet available (attempt {attempt}/5), "
                    "retrying in 5s..."
                )
                time.sleep(5)
        
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
        
        # Create system tray indicator if available (on main thread)
        use_gtk = (
            SYSTEM_TRAY_AVAILABLE
            and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
        )
        if use_gtk:
            try:
                self.system_tray = SystemTrayIndicator(daemon=self)
                self.system_tray.start_watching()
                self._gtk_mode = True
            except Exception as e:
                logger.warning(f"Could not start system tray: {e}")
                self.system_tray = None
                self._gtk_mode = False
        
        # Start command socket for IPC (force-sync, etc.)
        self._command_server = CommandServer(
            self.config.config_dir, self._on_force_sync_requested,
            version=__version__
        )
        self._command_server.start()

        # Set up file system monitoring
        self.event_handler = SyncEventHandler(self)
        self.observer = Observer()
        self.observer.schedule(self.event_handler, str(sync_dir), recursive=True)
        self.observer.start()
        
        # Start periodic sync thread
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()
        
        logger.info(f"Sync daemon started. Monitoring: {sync_dir}")
        
        if self._gtk_mode:
            # Install GLib-level signal handlers so SIGTERM/SIGINT are handled
            # inside the GTK main loop (Python signal handlers don't fire
            # while Gtk.main() blocks in C code).
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, self._on_glib_signal, signal.SIGTERM)
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, self._on_glib_signal, signal.SIGINT)
            
            # Enter GTK main loop on the main thread (blocks until quit)
            if self._running:
                logger.info("Entering GTK main loop on main thread")
                Gtk.main()
        else:
            # Headless mode: simple sleep loop (Python signal handlers work here)
            try:
                while self._running:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received, shutting down daemon loop")
        
        # After loop exits, ensure clean shutdown
        self.stop()
    
    def stop(self) -> None:
        """Stop the sync daemon (idempotent — safe to call multiple times)."""
        if self._stopping:
            return
        self._stopping = True
        
        logger.info("Stopping sync daemon...")
        self._running = False
        
        # Stop system tray
        if self.system_tray:
            try:
                self.system_tray.quit()
            except Exception as e:
                logger.debug(f"Error stopping system tray: {e}")
        
        # Stop command socket
        if self._command_server:
            try:
                self._command_server.stop()
            except Exception as e:
                logger.debug(f"Error stopping command socket: {e}")

        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
        
        if self._sync_thread:
            self._sync_thread.join(timeout=5)
        
        # Close config and state backend to release resources
        try:
            self.config.close()
        except Exception as e:
            logger.warning(f"Error closing config: {e}")
        
        logger.info("Sync daemon stopped")
    
    def _on_glib_signal(self, signum):
        """Handle SIGTERM/SIGINT inside the GTK main loop.

        Runs on the GTK main thread via ``GLib.unix_signal_add``. Calling
        ``Gtk.main_quit()`` *directly* from a unix-signal source does NOT cause
        ``Gtk.main()`` to return (the loop stays blocked), so the quit is
        deferred onto an idle callback, which reliably breaks the loop. Control
        then returns to ``start()`` which calls ``stop()``.
        """
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self._running = False
        GLib.idle_add(Gtk.main_quit)
        return GLib.SOURCE_REMOVE
    
    
    def _sync_loop(self) -> None:
        """Main sync loop (runs periodically)."""
        while self._running:
            try:
                # Process any pending rename/move events first so that
                # subsequent file-change events see the updated paths.
                if self.event_handler:
                    pending_moves = self.event_handler.get_pending_moves()
                    for src, (dst, is_dir) in pending_moves.items():
                        self._sync_move(src, dst, is_dir)

                # Process any pending file changes
                if self.event_handler:
                    pending = self.event_handler.get_pending_changes()
                    for path in pending:
                        self._sync_file(path)
                
                # Check for force sync signal (command socket or legacy file)
                if self._force_sync_requested.is_set() or self._check_force_sync_signal():
                    self._force_sync_requested.clear()
                    logger.info("Force sync triggered by user")
                    self._do_periodic_sync()
                # Periodic full sync check
                elif self._should_do_periodic_sync():
                    self._do_periodic_sync()

                # Periodic update check (background, non-blocking)
                self._check_for_updates()
                
            except Exception as e:
                logger.error(f"Error in sync loop: {e}", exc_info=True)
            
            # Wait for next sync opportunity, but wake early on watchdog
            # events or force sync requests (checked every second).
            deadline = time.monotonic() + self.config.sync_interval
            while self._running and time.monotonic() < deadline:
                if self._wakeup_event.wait(timeout=1.0):
                    self._wakeup_event.clear()
                    break
                if self.config.force_sync_path.exists():
                    break
    
    
    def _should_do_periodic_sync(self) -> bool:
        """Check if periodic sync should run.
        
        Returns:
            True if periodic sync needed
        """
        last_sync = self.state_mgr.last_sync
        if not last_sync:
            return True
        
        # Parse last sync time
        last_sync_dt = datetime.fromisoformat(last_sync)
        elapsed = (datetime.now() - last_sync_dt).total_seconds()
        
        return elapsed >= self.config.sync_interval
    
    def _on_force_sync_requested(self) -> None:
        """Callback from command socket when SYNC command received."""
        # Request a full sync (not just a wakeup) and break the wait loop.
        self._force_sync_requested.set()
        self._wakeup_event.set()

    def _check_force_sync_signal(self) -> bool:
        """Check if force sync signal file exists (legacy fallback).
        
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

    def _check_for_updates(self) -> None:
        """Check GitHub for a newer ODSC release (at most once per 24 h).

        If a newer version is found, emits a desktop notification via
        notify-send and logs at INFO level. Non-fatal errors are logged so a
        network outage never interrupts the sync loop silently.
        """
        now = time.monotonic()
        if now - self._last_update_check < UPDATE_CHECK_INTERVAL:
            return
        self._last_update_check = now

        try:
            req = urllib.request.Request(
                GITHUB_RELEASES_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"odsc/{__version__}",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            latest = data.get("tag_name", "").lstrip("v")
            if not latest:
                return

            installed_parts = [int(x) for x in __version__.split(".")]
            latest_parts = [int(x) for x in latest.split(".")]

            if latest_parts > installed_parts:
                msg = f"ODSC update available: v{latest} (installed: v{__version__})"
                logger.info(msg)
                try:
                    import subprocess
                    subprocess.run(
                        ["notify-send", "--app-name=ODSC", "ODSC Update Available",
                         f"Version {latest} is available.\n"
                         "Run: odsc update"],
                        check=False, timeout=5,
                    )
                except Exception as exc:
                    logger.debug(f"Could not show update notification: {exc}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.warning(f"Update check failed (non-fatal): {exc}")
        except Exception as exc:
            logger.error(f"Unexpected update-check failure: {exc}", exc_info=True)


    def _do_periodic_sync(self) -> None:
        """Perform periodic two-way sync of all files using delta query."""
        logger.info("Starting periodic two-way sync...")
        
        sync_dir = self.config.sync_directory
        
        # Reload state to pick up any GUI-written changes while preventing
        # watchdog workers from racing against the replacement.
        self.state_mgr.reload()
        
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

        # Detect files moved/renamed while the daemon was offline and mirror
        # them as server-side moves (content-identity match) instead of
        # re-uploading + orphaning a duplicate. Runs after folder sync so move
        # destinations' parent folders already exist remotely.
        self._detect_and_apply_moves(sync_dir, local_files)

        # Now sync files (folders already exist)
        all_remote_files = self._get_all_remote_files()
        self._sync_files(sync_dir, local_files, remote_files, all_remote_files)
        
        # Finalize sync
        self._finalize_sync()
        
        logger.info("Periodic sync completed")
    
    def _fetch_and_process_remote_changes(self, sync_dir: Path) -> Optional[Dict[str, Any]]:
        """Fetch changes from OneDrive and process them.
        
        Returns:
            Dictionary of remote files, or None if error occurred
        """
        delta_token = self.state_mgr.delta_token
        
        # Fetch changes using delta query
        logger.info("Fetching changes from OneDrive using delta query...")
        try:
            if delta_token:
                changes, new_delta_token = self.client.get_delta(delta_token)
                logger.info(f"Incremental sync: {len(changes)} changes detected")
            else:
                changes, new_delta_token = self.client.get_delta(None)
                logger.info(f"Initial sync: {len(changes)} total items")
            
            self.state_mgr.delta_token = new_delta_token
            
        except Exception as exc:
            if get_http_status(exc) == 410:
                # HTTP 410 Gone (resyncRequired): the delta token is no longer
                # valid, so it MUST be reset to force a full resync next cycle.
                logger.warning(
                    "Delta token rejected by OneDrive (HTTP 410); "
                    "resetting for a full resync"
                )
                self.state_mgr.delta_token = None
            else:
                # Transient/other failure: preserve the existing delta token so
                # the next cycle resumes incrementally. Clearing it here would
                # force a full resync whose from-scratch delta carries no
                # deletion tombstones, which can cause remote deletions to be
                # missed and stale cache entries to survive.
                self._log_operation_error("Failed to fetch changes", exc, exc_info=True)
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
        for path, cached_item in self.state_mgr.all_cache_items():
            if cached_item.get('id') == item_id:
                is_folder = 'folder' in cached_item or cached_item.get('is_folder', False)
                item_type = "folder" if is_folder else "file"
                logger.info(f"{item_type.capitalize()} deleted on OneDrive: {path}")
                
                # Track this deletion to prevent re-upload
                if hasattr(self, '_deleted_from_remote'):
                    self._deleted_from_remote.add(path)
                    logger.debug(f"Added {path} to deleted tracking set")

                # Record a durable tombstone (files only) so a remotely-deleted
                # file is never re-uploaded even if other state is lost. Carry
                # the deleted version's content hash so a later user-created file
                # at the same path is distinguished from the lingering copy.
                if not is_folder:
                    self.state_mgr.add_tombstone(
                        path, origin='remote',
                        etag=cached_item.get('eTag'),
                        quick_xor=cached_item.get('quickXorHash'),
                    )

                # Delete local file/folder if it exists
                local_path = self.config.sync_directory / path
                if local_path.exists():
                    trashed = self._move_to_recycle_bin(local_path, path)
                else:
                    trashed = True

                if trashed:
                    # Remove from cache and state only once the local copy is gone.
                    self.state_mgr.remove_file_entry(path)
                    self.state_mgr.clear_deletion_failure(path)
                    # Deletion fully reconciled — retire the tombstone.
                    self.state_mgr.remove_tombstone(path)
                else:
                    # Trash failed and the local file survives. Keep the sync-state
                    # entry so it is not re-uploaded as a new file. For files, also
                    # drop the stale cache entry so the next sync sees it as
                    # local-only and retries the trash (decision -> 'recycle').
                    # For folders, keep the cache entry: folder reconciliation
                    # relies on it to retry the deletion.
                    self.state_mgr.increment_deletion_failure(path)
                    if not is_folder:
                        self.state_mgr.remove_cache_entry(path)
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
        
        for path in list(self._deleted_from_remote):
            local_path = sync_dir / path
            
            # Validate path is within sync directory (protect against symlink attacks)
            try:
                validate_sync_path(path, sync_dir)
            except SecurityError as e:
                logger.error(f"Path validation failed for deletion: {path} - {e}")
                continue
            
            # Check if deletion succeeded (use try/except to avoid TOCTOU)
            try:
                # Try to stat the file - if it doesn't exist, this will raise FileNotFoundError
                local_path.stat()
            except FileNotFoundError:
                logger.debug(f"Deletion verified successful: {path}")
                self.state_mgr.clear_deletion_failure(path)  # Clear any previous failure count
                continue
            
            # Deletion failed or incomplete — log and skip.
            # Do NOT permanently delete; leave file in place for safety.
            logger.warning(
                f"Deletion incomplete (trash may have failed): {path}. "
                "File left in place to prevent data loss."
            )
    
    def _process_remote_folder(self, item: Dict[str, Any], sync_dir: Path) -> None:
        """Process a folder from OneDrive delta."""
        try:
            # Skip the drive root itself
            if 'root' in item or item.get('name') == 'root':
                logger.debug("Skipping drive root object")
                return
            
            full_path = extract_item_path(item)
            validate_sync_path(full_path, sync_dir)
            # Normalize with is_folder=True (mirrors _process_remote_file). Without
            # this, a folder first seen in the CURRENT delta cycle is absent from
            # all_remote_folders() (which filters on is_folder) until the next
            # reload, causing _delete_folders_removed_from_remote to wrongly trash
            # the matching local folder.
            self.state_mgr.set_cache_entry(full_path, {**item, 'is_folder': True})
        except SecurityError as exc:
            logger.error(f"Skipping unsafe folder: {exc}")
        except Exception as exc:
            logger.error(f"Failed to process remote folder item: {exc}", exc_info=True)
    
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
            # Capture OneDrive's content hash when present. Stored additively so
            # future content-addressed change detection can use it; it does not
            # affect any current sync decision.
            quick_xor = extract_quickxorhash(item)
            if quick_xor:
                metadata['quickXorHash'] = quick_xor
            
            # Update cache
            self.state_mgr.set_cache_entry(full_path, {**metadata, 'is_folder': False})
            # The path exists on OneDrive again — clear any stale deletion
            # tombstone (handles a file that was deleted then re-created remotely).
            self.state_mgr.remove_tombstone(full_path)

            return {'path': full_path, 'metadata': metadata}
            
        except SecurityError as exc:
            logger.error(f"Skipping unsafe remote path: {exc}")
            return None
        except Exception as exc:
            logger.error(f"Error processing remote item: {exc}", exc_info=True)
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
        all_remote_files = self.state_mgr.all_remote_files()
        logger.info(f"Total remote files in cache: {len(all_remote_files)}")
        return all_remote_files
    
    def _get_all_remote_folders(self) -> Dict[str, Any]:
        """Get all remote folders from cache."""
        all_remote_folders = {
            path: meta
            for path, meta in self.state_mgr.all_remote_folders().items()
            if 'root' not in meta and path != 'root'
        }
        logger.info(f"Total remote folders in cache: {len(all_remote_folders)}")
        return all_remote_folders
    
    def _sync_files(self, sync_dir: Path, local_files: Dict, remote_files: Dict, all_remote_files: Dict) -> None:
        """Sync files between local and remote (optimized)."""
        start_time = time.time()
        
        # OPTIMIZATION: Only process files that actually need syncing
        # Instead of iterating through ALL 25K+ remote files, we:
        # 1. Check all local files (small set - ~80 files)
        # 2. Check only files changed remotely in this delta (small set)
        # 3. Skip everything else (already in sync)
        
        # Determine all actions sequentially (reads self.state, no I/O).
        pending_actions = []
        processed = set()
        
        for rel_path, local_info in local_files.items():
            remote_info = remote_files.get(rel_path) or all_remote_files.get(rel_path)
            state_entry = self.state_mgr.get_file_entry(rel_path)
            try:
                action = self._determine_sync_action(rel_path, local_info, remote_info, state_entry)
                pending_actions.append((action, rel_path, local_info, remote_info))
                processed.add(rel_path)
            except Exception as e:
                logger.error(f"Failed to determine sync action for {rel_path}: {e}", exc_info=True)
        
        for rel_path, remote_info in remote_files.items():
            if rel_path in processed:
                continue
            local_info = local_files.get(rel_path)
            state_entry = self.state_mgr.get_file_entry(rel_path)
            try:
                action = self._determine_sync_action(rel_path, local_info, remote_info, state_entry)
                pending_actions.append((action, rel_path, local_info, remote_info))
                processed.add(rel_path)
            except Exception as e:
                logger.error(f"Failed to determine sync action for {rel_path}: {e}", exc_info=True)
        
        # Execute actions in parallel using a thread pool.
        sync_count: Dict[str, int] = {'upload': 0, 'download': 0, 'skip': 0, 'conflict': 0, 'recycle': 0}
        max_workers = self.config.max_sync_workers
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_action = {
                executor.submit(
                    self._execute_sync_action, action, rel_path, sync_dir, local_info, remote_info
                ): (action, rel_path)
                for action, rel_path, local_info, remote_info in pending_actions
            }
            for future in as_completed(future_to_action):
                action, rel_path = future_to_action[future]
                try:
                    future.result()
                    sync_count[action] = sync_count.get(action, 0) + 1
                except Exception as e:
                    logger.error(f"Failed to {action} {rel_path}: {e}", exc_info=True)
        
        elapsed = time.time() - start_time
        logger.info(f"File sync completed in {elapsed:.2f}s: "
                   f"{sync_count['upload']} uploaded, {sync_count['download']} downloaded, "
                   f"{sync_count['skip']} skipped, {sync_count['conflict']} conflicts, "
                   f"{sync_count['recycle']} recycled ({len(processed)} total processed)")
        
        # Cleanup stale state entries (files deleted locally that no longer exist remotely)
        self._cleanup_stale_state(local_files, all_remote_files)
    
    def _cleanup_stale_state(self, local_files: Dict, all_remote_files: Dict) -> None:
        """Remove stale entries from sync state.
        
        Removes state entries for files that:
        - Don't exist locally (user deleted them)
        - Don't exist remotely (deleted from OneDrive)
        
        This prevents unbounded state growth from files that were once synced
        but are now gone from both sides.
        
        Args:
            local_files: Dict of local files
            all_remote_files: Dict of all remote files
        """
        stale_paths = []
        
        for path in self.state_mgr.all_tracked_paths():
            # If file exists locally or remotely, keep the state
            if path in local_files or path in all_remote_files:
                continue
            
            # File doesn't exist on either side - state is stale
            stale_paths.append(path)
        
        if stale_paths:
            for path in stale_paths:
                self.state_mgr.remove_file_entry(path)
            logger.debug(f"Cleaned up {len(stale_paths)} stale state entries")
    
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
    
    def _detect_and_apply_moves(self, sync_dir: Path, local_files: Dict) -> None:
        """Mirror offline file moves/renames as server-side moves on OneDrive.

        A move performed while the daemon was down appears next sync as the old
        path missing locally (but still on OneDrive) plus a new local path not
        yet on OneDrive. Re-uploading the new path while leaving the old one
        duplicates the file on OneDrive and re-transfers its bytes. When a
        previously-synced remote file's content (size + QuickXorHash) matches a
        new local file, issue a single PATCH move instead.

        This relocates the existing OneDrive item — it never deletes data from
        OneDrive. If the move fails (e.g. a name collision), the new path simply
        falls through to a normal upload (no regression).
        """
        all_remote = self.state_mgr.all_remote_files()

        # Source candidates: previously-synced remote files now absent locally,
        # indexed by size, requiring a known content hash for a safe match.
        sources_by_size: Dict[int, List[tuple]] = {}
        for rpath, meta in all_remote.items():
            if rpath in local_files:
                continue
            state = self.state_mgr.get_file_entry(rpath)
            if not (state.get('downloaded') or state.get('eTag')):
                continue  # never had a local copy -> not a move source
            rhash = meta.get('quickXorHash')
            if not rhash or not meta.get('id'):
                continue  # need a content hash + id to move safely
            sources_by_size.setdefault(meta.get('size'), []).append((rpath, meta, rhash))

        if not sources_by_size:
            return

        for lpath, info in list(local_files.items()):
            if lpath in all_remote:
                continue
            state = self.state_mgr.get_file_entry(lpath)
            if state.get('eTag') or state.get('downloaded'):
                continue  # already tracked -> not a fresh move destination
            candidates = sources_by_size.get(info['size'])
            if not candidates:
                continue
            try:
                lhash = quickxorhash_file(info['path'])
            except OSError:
                continue
            match = next((c for c in candidates if c[2] == lhash), None)
            if not match:
                continue
            if self._apply_server_side_move(match[0], match[1], lpath):
                candidates.remove(match)

    def _apply_server_side_move(self, src_path: str, src_meta: Dict, dst_path: str) -> bool:
        """PATCH-move the OneDrive item from *src_path* to *dst_path*. Returns success."""
        item_id = src_meta.get('id')
        if not item_id:
            return False
        normalized = dst_path.replace('\\', '/')
        new_name = normalized.rsplit('/', 1)[-1]
        new_parent = normalized.rsplit('/', 1)[0] if '/' in normalized else ''
        try:
            self.client.move_item(item_id, new_name, new_parent)
        except Exception as exc:
            self._log_operation_error(
                f"Server-side move failed {src_path} -> {dst_path}", exc
            )
            return False
        # Keep state consistent: the tracked item now lives at the new path.
        self.state_mgr.rename_entry(src_path, dst_path)
        self.state_mgr.remove_tombstone(src_path)
        logger.info(f"Detected offline move: {src_path} -> {dst_path} (server-side PATCH)")
        return True

    def _upload_is_redundant(self, rel_path: str, local_path: Path, mtime: float, size: int) -> bool:
        """Return True if the local file's content matches the last synced hash.

        This suppresses redundant uploads — most importantly the self-write echo
        where downloading a file triggers a watchdog event that would otherwise
        re-upload identical content, and no-op touches that only change mtime.
        On a match the recorded mtime/size are refreshed so future cycles
        short-circuit cheaply. Never skips a real content change: if no hash was
        recorded, or it differs, or hashing fails, the upload proceeds.
        """
        state_entry = self.state_mgr.get_file_entry(rel_path)
        stored_hash = state_entry.get('quickXorHash') if state_entry else None
        if not stored_hash:
            return False
        try:
            local_hash = quickxorhash_file(local_path)
        except OSError:
            return False
        if local_hash == stored_hash:
            logger.debug(f"Content unchanged (hash match), skipping upload: {rel_path}")
            self.state_mgr.mark_file_unchanged(rel_path, mtime, size)
            return True
        return False

    def _resolve_tombstone_before_upload(self, rel_path: str, local_path: Path) -> bool:
        """Return True if an upload should be replaced by a recycle.

        Resurrection guard: if *rel_path* has a remote-deletion tombstone and the
        local file's content still matches the deleted version's hash, the file
        is the lingering deleted copy — recycle it instead of re-uploading it to
        OneDrive. If the content differs (the user created a new file at the same
        path) or the hash is unknown, the tombstone is cleared and the upload
        proceeds, so a genuine new file is never trashed or blocked.
        """
        tomb = self.state_mgr.get_tombstone(rel_path)
        if not tomb or tomb.get('origin') != 'remote':
            return False
        tomb_hash = tomb.get('quickXorHash')
        if tomb_hash:
            try:
                local_hash = quickxorhash_file(local_path)
            except OSError:
                local_hash = None
            if local_hash == tomb_hash:
                logger.info(
                    f"{rel_path} matches a remote-deletion tombstone; "
                    "recycling instead of re-uploading"
                )
                self._recycle_remote_deleted_file(rel_path, self.config.sync_directory)
                return True
        # Different content (or no recorded hash): the user re-created the file.
        self.state_mgr.remove_tombstone(rel_path)
        return False

    def _upload_file(self, rel_path: str, local_info: Dict) -> None:
        """Upload a local file to OneDrive."""
        if self._upload_is_redundant(rel_path, local_info['path'], local_info['mtime'], local_info['size']):
            return
        if self._resolve_tombstone_before_upload(rel_path, local_info['path']):
            return
        logger.info(f"Uploading: {rel_path}")
        try:
            metadata = self.client.upload_file(local_info['path'], rel_path)
            self.state_mgr.set_file_entry(rel_path, local_info['mtime'], local_info['size'], metadata)
        except Exception as upload_err:
            self._log_operation_error(f"Upload failed for {rel_path}", upload_err)
            self.state_mgr.set_file_entry(rel_path, local_info['mtime'], local_info['size'], error=str(upload_err))
    
    def _download_file(self, rel_path: str, sync_dir: Path, remote_info: Dict) -> None:
        """Download a file from OneDrive."""
        logger.info(f"Downloading updated version: {rel_path}")
        try:
            local_path = validate_sync_path(rel_path, sync_dir)
            metadata = self.client.download_file(
                remote_info['id'], local_path,
                chunk_size=self.config.download_chunk_size,
            )
            try:
                mtime = local_path.stat().st_mtime
            except FileNotFoundError:
                mtime = 0.0
            self.state_mgr.set_file_entry(rel_path, mtime, remote_info['size'], remote_info)
        except Exception as download_err:
            self._log_operation_error(f"Download failed for {rel_path}", download_err)
    
    def _recycle_remote_deleted_file(self, rel_path: str, sync_dir: Path) -> None:
        """Handle a file that was deleted remotely."""
        logger.warning(f"File deleted remotely, moving to recycle bin: {rel_path}")
        local_path = validate_sync_path(rel_path, sync_dir)
        if self._move_to_recycle_bin(local_path, rel_path):
            self.state_mgr.remove_file_entry(rel_path)
            self.state_mgr.clear_deletion_failure(rel_path)
            self.state_mgr.remove_tombstone(rel_path)  # deletion reconciled
        else:
            # Keep the sync-state entry so the surviving local file is not
            # re-uploaded, but drop the cache entry so the next sync reclassifies
            # it as local-only and retries the trash (decision -> 'recycle').
            self.state_mgr.increment_deletion_failure(rel_path)
            self.state_mgr.remove_cache_entry(rel_path)
    
    def _handle_file_conflict(self, rel_path: str, sync_dir: Path, remote_info: Dict) -> None:
        """Handle a file conflict by keeping both versions."""
        logger.warning(f"CONFLICT detected for {rel_path} - keeping both versions")
        conflict_rel = self._next_conflict_name(rel_path, sync_dir)
        conflict_path = validate_sync_path(conflict_rel, sync_dir)
        metadata = self.client.download_file(
            remote_info['id'], conflict_path,
            chunk_size=self.config.download_chunk_size,
        )
        logger.info(f"Saved remote version as: {conflict_path}")
        self.state_mgr.add_conflict(rel_path, conflict_rel, remote_info)
        self._notify_conflict(rel_path)

    def _notify_conflict(self, rel_path: str) -> None:
        """Send a desktop notification about a file conflict."""
        try:
            import subprocess
            subprocess.run(
                ["notify-send", "--app-name=ODSC", "ODSC: File Conflict",
                 f"Both local and remote versions of '{rel_path}' have changed.\n"
                 "Both copies have been kept. Check for .conflict files."],
                check=False, timeout=5,
            )
        except Exception as exc:
            logger.debug(f"Could not show conflict notification: {exc}")

    def _next_conflict_name(self, rel_path: str, sync_dir: Path) -> str:
        """Generate a unique .conflict filename that doesn't already exist.

        Returns:
            Relative path like 'file.txt.conflict' or 'file.txt.conflict.2'
        """
        candidate = f"{rel_path}.conflict"
        if not (sync_dir / candidate).exists():
            return candidate
        n = 1
        while True:
            n += 1
            candidate = f"{rel_path}.conflict.{n}"
            if not (sync_dir / candidate).exists():
                return candidate

    def _maybe_clear_conflict(self, rel_path: str) -> None:
        """Clear a conflict record if the deleted file was a .conflict file."""
        conflicts = self.state_mgr.all_conflicts()
        for original, info in conflicts.items():
            if info.get("conflict_path") == rel_path:
                logger.info(f"Conflict resolved (conflict file removed): {original}")
                self.state_mgr.remove_conflict(original)
                self.state_mgr.save()
                return
    
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
            if self.state_mgr.get_cache_entry(folder_path) is not None:
                # Folder was synced before, check if it still exists on OneDrive
                if folder_path not in all_remote_folders:
                    # Folder was deleted from OneDrive
                    folders_to_delete.append(folder_path)
        
        for folder_path in folders_to_delete:
            try:
                local_path = validate_sync_path(folder_path, sync_dir)
                logger.info(f"Folder deleted from OneDrive, removing locally: {folder_path}")
                if self._move_to_recycle_bin(local_path, folder_path):
                    del local_folders[folder_path]
                    self.state_mgr.remove_file_entry(folder_path)
                    self.state_mgr.clear_deletion_failure(folder_path)
                else:
                    # Trash failed; keep the folder tracked so its files are not
                    # re-uploaded, and retry on a later sync.
                    self.state_mgr.increment_deletion_failure(folder_path)
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
                    self.state_mgr.set_cache_entry(folder_path, metadata)
                    logger.info(f"Folder created on OneDrive: {folder_path}")
                except Exception as exc:
                    self._log_operation_error(f"Failed to create folder {folder_path} on OneDrive", exc)
    
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
        self.state_mgr.mark_sync_complete()
        self.state_mgr.save()
    
    def _move_to_recycle_bin(self, local_path: Path, rel_path: str) -> bool:
        """Move file or folder to system recycle bin/trash.

        Args:
            local_path: Full path to the local file or folder
            rel_path: Relative path for logging

        Returns:
            ``True`` if the item is no longer present locally (successfully
            trashed, or it did not exist). ``False`` if a trash error left the
            file in place — callers must then keep sync state so the surviving
            local file is not mistaken for a new file and re-uploaded.
        """
        try:
            if local_path.exists():
                item_type = "folder" if local_path.is_dir() else "file"
                send2trash(str(local_path))
                logger.info(f"Moved {item_type} to recycle bin: {rel_path}")
                return True
            else:
                logger.warning(f"Item not found for recycling: {rel_path}")
                return True
        except Exception as e:
            logger.error(f"Failed to move {rel_path} to recycle bin: {e}")
            # Do NOT fall back to permanent deletion — user data must not be lost.
            # Leave the file in place and log for manual resolution.
            logger.error(
                f"File left in place (trash unavailable): {rel_path}. "
                "Resolve manually or ensure trash service is working."
            )
            return False
    
    def _determine_sync_action(self, rel_path: str, local_info: Optional[Dict],
                               remote_info: Optional[Dict], state_entry: Dict) -> str:
        """Determine what sync action to take for a file.

        Delegates to :class:`~odsc.sync.decision_engine.SyncDecisionEngine`,
        passing the set of paths deleted from OneDrive during this sync cycle.

        Returns:
            Action: 'upload', 'download', 'conflict', 'recycle', or 'skip'
        """
        deleted = getattr(self, '_deleted_from_remote', None)
        return self.decision_engine.determine_action(
            rel_path, local_info, remote_info, state_entry, deleted
        )


    def _sync_move(self, src_path: Path, dst_path: Path, is_dir: bool = False) -> None:
        """Handle a local rename or move by mirroring it on OneDrive.

        When watchdog fires a ``FileMovedEvent`` or ``DirMovedEvent`` we have
        the old and new paths atomically, so we can issue a single PATCH
        request on OneDrive instead of creating a duplicate.

        Fallback: if the old item isn't tracked in the cache (was never
        synced) we queue the destination for a normal upload.

        Args:
            src_path: Original absolute path (before the rename/move).
            dst_path: New absolute path (after the rename/move).
            is_dir:   True when a directory (tree) was moved.
        """
        sync_dir = self.config.sync_directory

        # Resolve source relative path (must be inside sync dir)
        try:
            src_rel = str(src_path.relative_to(sync_dir))
        except ValueError:
            logger.debug(f"Move source outside sync dir, ignoring: {src_path}")
            return

        # Resolve destination relative path
        try:
            dst_rel = str(dst_path.relative_to(sync_dir))
        except ValueError:
            # Destination is outside the sync folder — treat as local-only deletion
            logger.info(f"Item moved out of sync directory, leaving copy on OneDrive: {src_rel}")
            if is_dir:
                # Stop tracking the whole subtree. Renaming child paths to an
                # empty prefix would corrupt state (paths gain a leading
                # separator and collide at the root), so remove them instead.
                self.state_mgr.remove_entries_with_prefix(src_rel)
            else:
                self.state_mgr.remove_file_entry(src_rel)
            self.state_mgr.save()
            return

        # Look up OneDrive item ID from cache
        cache_entry = self.state_mgr.get_cache_entry(src_rel)
        if cache_entry is None or not cache_entry.get("id"):
            # Never synced — queue destination path for a regular upload/scan
            logger.info(f"Moved item not yet tracked, queuing for upload: {dst_path}")
            if self.event_handler and not is_dir:
                self.event_handler._queue_change(dst_path)
            return

        item_id = cache_entry["id"]
        new_name = dst_path.name
        dst_parent = str(Path(dst_rel).parent)
        src_parent = str(Path(src_rel).parent)
        # '.' means root of sync folder
        dst_parent = "" if dst_parent == "." else dst_parent
        src_parent = "" if src_parent == "." else src_parent
        same_parent = (src_parent == dst_parent)

        try:
            if same_parent:
                self.client.move_item(item_id, new_name)
                logger.info(f"Renamed on OneDrive: {src_rel!r} → {dst_rel!r}")
            else:
                self.client.move_item(item_id, new_name, dst_parent)
                logger.info(f"Moved on OneDrive: {src_rel!r} → {dst_rel!r}")

            # Update state to reflect new path(s)
            if is_dir:
                renamed = self.state_mgr.rename_entries_with_prefix(src_rel, dst_rel)
                logger.debug(f"Renamed {renamed} state entries for directory move")
            else:
                self.state_mgr.rename_entry(src_rel, dst_rel)
            self.state_mgr.save()

        except Exception as e:
            logger.error(f"Failed to move {src_rel!r} → {dst_rel!r}: {e}", exc_info=True)
            # Fall back: queue destination for a normal upload
            if self.event_handler and not is_dir:
                self.event_handler._queue_change(dst_path)

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
            # Auto-clear conflict if a .conflict file was removed
            self._maybe_clear_conflict(str(rel_path))
            return
        
        try:
            # Stat before upload — file may disappear after the upload completes
            # (e.g. transient .tmp files created by other applications)
            try:
                mtime = path.stat().st_mtime
                size = path.stat().st_size
            except FileNotFoundError:
                logger.info(f"File vanished before upload, skipping: {rel_path}")
                return

            # Suppress redundant uploads (self-write echo after a download, or a
            # no-op touch) when the content hash matches the last synced value.
            if self._upload_is_redundant(str(rel_path), path, mtime, size):
                self.state_mgr.persist_file(str(rel_path))
                return

            # Resurrection guard: a remotely-deleted file that lingers locally
            # must not be re-uploaded (unless the user replaced its content).
            if self._resolve_tombstone_before_upload(str(rel_path), path):
                self.state_mgr.save()  # entry removed/renamed -> full save
                return

            # Upload file
            metadata = self.client.upload_file(path, str(rel_path))
            
            # Update state - clear any previous error (incremental single-row write)
            self.state_mgr.set_file_entry(str(rel_path), mtime, size, metadata)
            self.state_mgr.persist_file(str(rel_path))
            
            logger.info(f"Synced file: {rel_path}")
            
        except Exception as exc:
            error_msg = str(exc)
            self._log_operation_error(f"Failed to sync {rel_path}", exc, exc_info=True)
            
            # Track failed upload — use already-captured mtime/size if available,
            # otherwise fall back to zeros (file may have been deleted mid-upload)
            try:
                entry_mtime = mtime
                entry_size = size
            except NameError:
                entry_mtime, entry_size = 0.0, 0
            self.state_mgr.set_file_entry(str(rel_path), entry_mtime, entry_size, error=error_msg)
            self.state_mgr.persist_file(str(rel_path))


def main():
    """Main entry point for daemon."""
    config = Config()
    
    pid_file = config.config_dir / "odsc.pid"
    
    # Single-instance guard: atomically create PID file to avoid TOCTOU race
    for attempt in range(2):
        try:
            fd = os.open(str(pid_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                existing_pid = int(pid_file.read_text().strip())
                # Signal 0 checks existence without killing
                os.kill(existing_pid, 0)
                logger.error(
                    f"Daemon already running with PID {existing_pid}. "
                    "Remove ~/.config/odsc/odsc.pid if this is incorrect."
                )
                return
            except (ProcessLookupError, ValueError):
                # Stale PID file — previous instance is gone
                pid_file.unlink(missing_ok=True)
    else:
        logger.error("Failed to acquire PID file lock after retrying.")
        return
    try:
        daemon = SyncDaemon(config)
        daemon.start()
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
