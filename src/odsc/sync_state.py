"""SyncStateManager — thread-safe owner of all daemon sync state.

The daemon previously maintained a raw ``self.state`` dict protected by
ad-hoc ``with self._state_lock`` blocks scattered across 30+ call sites.
This class centralises every state read/write behind typed methods, owns
the lock, and handles load/save so the daemon never touches the backend
directly for state purposes.

State Structure
---------------
``files``
    Per-file sync tracking (mtime, size, eTag, download status, errors).
``file_cache``
    Complete OneDrive metadata snapshot from the last delta query.
``delta_token``
    OneDrive delta query continuation token.
``last_sync``
    ISO-8601 timestamp of the last completed sync.
``_deletion_failures``
    Per-path counter of consecutive local-deletion failures.
"""

import logging
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SyncStateManager:
    """Thread-safe manager for daemon sync state.

    Args:
        backend_load: Callable that returns the full state dict from the
            persistent backend (e.g. ``config.load_state``).
        backend_save: Callable that persists the full state dict
            (e.g. ``config.save_state``).
    """

    def __init__(self, backend_load, backend_save) -> None:
        self._load = backend_load
        self._save = backend_save
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def load(self) -> None:
        """Load state from the persistent backend (replaces in-memory state)."""
        loaded = self._load()
        with self._lock:
            self._state = loaded
            self._ensure_initialized()

    def save(self) -> None:
        """Persist current in-memory state to the backend."""
        with self._lock:
            snapshot = dict(self._state)
        self._save(snapshot)

    def reload(self) -> None:
        """Reload state from the backend under the lock.

        Used at the start of each periodic sync to pick up any GUI-written
        changes while preventing concurrent watchdog writes from racing
        against the replacement.
        """
        loaded = self._load()
        with self._lock:
            self._state = loaded
            self._ensure_initialized()

    # ------------------------------------------------------------------ #
    # Top-level keys                                                       #
    # ------------------------------------------------------------------ #

    @property
    def delta_token(self) -> Optional[str]:
        with self._lock:
            return self._state.get("delta_token")

    @delta_token.setter
    def delta_token(self, value: Optional[str]) -> None:
        with self._lock:
            self._state["delta_token"] = value

    @property
    def last_sync(self) -> Optional[str]:
        with self._lock:
            return self._state.get("last_sync")

    def mark_sync_complete(self) -> None:
        """Record the current time as the last-sync timestamp."""
        with self._lock:
            self._state["last_sync"] = datetime.now().isoformat()

    # ------------------------------------------------------------------ #
    # Files (sync tracking)                                                #
    # ------------------------------------------------------------------ #

    def get_file_entry(self, rel_path: str) -> Dict[str, Any]:
        """Return the sync-state entry for *rel_path*, or ``{}`` if absent."""
        with self._lock:
            return dict(self._state["files"].get(rel_path, {}))

    def set_file_entry(
        self,
        rel_path: str,
        mtime: float,
        size: int,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Write (or update) the sync-state entry for *rel_path*."""
        entry: Dict[str, Any] = {
            "mtime": mtime,
            "size": size,
            "downloaded": True,
        }
        if error:
            entry["upload_error"] = error
        else:
            entry["eTag"] = metadata.get("eTag", "") if metadata else ""
            entry["remote_modified"] = (
                metadata.get("lastModifiedDateTime", "") if metadata else ""
            )
            entry["upload_error"] = None

        with self._lock:
            self._state["files"][rel_path] = entry

    def remove_file_entry(self, rel_path: str) -> None:
        """Remove *rel_path* from both ``files`` and ``file_cache``."""
        with self._lock:
            self._state["files"].pop(rel_path, None)
            self._state["file_cache"].pop(rel_path, None)
            logger.debug(f"Removed {rel_path} from state")

    def all_tracked_paths(self) -> List[str]:
        """Return a snapshot list of all tracked file paths."""
        with self._lock:
            return list(self._state["files"].keys())

    def patch_file_entries(self, updates: Dict[str, Dict[str, Any]]) -> None:
        """Merge *updates* into the ``files`` dict atomically.

        Used by the GUI after a batch download to record multiple entries
        without a separate load/save cycle that could race with the daemon.
        """
        with self._lock:
            self._state["files"].update(updates)

    def mark_file_not_downloaded(self, rel_path: str) -> None:
        """Set ``downloaded=False`` for *rel_path* (used by GUI on remove)."""
        with self._lock:
            entry = self._state["files"].get(rel_path)
            if entry is not None:
                entry["downloaded"] = False

    # ------------------------------------------------------------------ #
    # File cache (OneDrive metadata snapshot)                              #
    # ------------------------------------------------------------------ #

    def get_cache_entry(self, path: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._state["file_cache"].get(path)
            return dict(entry) if entry else None

    def set_cache_entry(self, path: str, metadata: Dict[str, Any]) -> None:
        with self._lock:
            self._state["file_cache"][path] = metadata

    def all_cache_items(self) -> List[Tuple[str, Dict[str, Any]]]:
        """Return a snapshot list of ``(path, metadata)`` cache entries."""
        with self._lock:
            return list(self._state["file_cache"].items())

    def all_remote_files(self) -> Dict[str, Dict[str, Any]]:
        """Return all non-folder cache entries."""
        with self._lock:
            return {
                p: dict(m)
                for p, m in self._state["file_cache"].items()
                if not m.get("is_folder", False)
            }

    def all_remote_folders(self) -> Dict[str, Dict[str, Any]]:
        """Return all folder cache entries."""
        with self._lock:
            return {
                p: dict(m)
                for p, m in self._state["file_cache"].items()
                if m.get("is_folder", False)
            }

    # ------------------------------------------------------------------ #
    # Deletion failure tracking                                            #
    # ------------------------------------------------------------------ #

    def get_deletion_failure_count(self, path: str) -> int:
        with self._lock:
            return self._state.setdefault("_deletion_failures", {}).get(path, 0)

    def increment_deletion_failure(self, path: str) -> int:
        """Increment and return the failure count for *path*."""
        with self._lock:
            failures = self._state.setdefault("_deletion_failures", {})
            failures[path] = failures.get(path, 0) + 1
            return failures[path]

    def clear_deletion_failure(self, path: str) -> None:
        with self._lock:
            self._state.get("_deletion_failures", {}).pop(path, None)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _ensure_initialized(self) -> None:
        """Ensure required top-level dicts are present (must hold lock)."""
        if "files" not in self._state:
            self._state["files"] = {}
        if "file_cache" not in self._state:
            self._state["file_cache"] = {}
