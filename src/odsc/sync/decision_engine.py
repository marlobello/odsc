"""Pure sync-decision logic, extracted from the daemon for testability.

:class:`SyncDecisionEngine` answers a single question: given the local view,
the remote view, and the last known sync state for one path, what action
should the daemon take? It has no side effects and no I/O — the only external
dependency is a ``cache_lookup`` callable used to tell whether a path was
previously known to exist on OneDrive.

Returned actions: ``'upload'``, ``'download'``, ``'conflict'``,
``'recycle'`` (move local copy to trash), or ``'skip'``.
"""

import logging
from typing import Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


class SyncDecisionEngine:
    """Decides the sync action for a single path.

    Args:
        cache_lookup: Callable mapping a relative path to its cached OneDrive
            metadata (or ``None`` if the path was never cached). Used to
            distinguish a brand-new local file from one that existed remotely
            and was deleted.
    """

    def __init__(self, cache_lookup: Callable[[str], Optional[Dict]]) -> None:
        self._cache_lookup = cache_lookup

    def determine_action(
        self,
        rel_path: str,
        local_info: Optional[Dict],
        remote_info: Optional[Dict],
        state_entry: Dict,
        deleted_from_remote: Optional[Set[str]] = None,
    ) -> str:
        """Return the sync action for *rel_path*.

        Args:
            rel_path: Relative file path.
            local_info: Local file info, or ``None`` if absent locally.
            remote_info: Remote file info, or ``None`` if absent remotely.
            state_entry: Last known sync state (``{}`` if untracked).
            deleted_from_remote: Set of paths deleted from OneDrive during the
                current sync cycle.
        """
        if deleted_from_remote and rel_path in deleted_from_remote:
            logger.info(f"{rel_path} was deleted from OneDrive in this sync, moving to recycle bin")
            return 'recycle'

        if self._is_local_only(local_info, remote_info):
            return self._handle_local_only_file(rel_path, state_entry)

        if self._is_remote_only(local_info, remote_info):
            return self._handle_remote_only_file(rel_path, state_entry)

        if self._exists_both_places(local_info, remote_info):
            return self._handle_file_exists_both(rel_path, local_info, remote_info, state_entry)

        return 'skip'

    # ------------------------------------------------------------------ #
    # Presence checks                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_local_only(local_info: Optional[Dict], remote_info: Optional[Dict]) -> bool:
        return local_info is not None and remote_info is None

    @staticmethod
    def _is_remote_only(local_info: Optional[Dict], remote_info: Optional[Dict]) -> bool:
        return remote_info is not None and local_info is None

    @staticmethod
    def _exists_both_places(local_info: Optional[Dict], remote_info: Optional[Dict]) -> bool:
        return local_info is not None and remote_info is not None

    # ------------------------------------------------------------------ #
    # Scenario handlers                                                    #
    # ------------------------------------------------------------------ #

    def _handle_local_only_file(self, rel_path: str, state_entry: Dict) -> str:
        """File exists only locally: ``'upload'`` if new, ``'recycle'`` if it was deleted remotely."""
        if not state_entry:
            if self._cache_lookup(rel_path) is not None:
                logger.info(f"{rel_path} was deleted remotely (found in cache), moving to recycle bin")
                return 'recycle'
            return 'upload'

        if state_entry.get('eTag'):
            logger.info(f"{rel_path} was deleted remotely, moving to recycle bin")
            return 'recycle'

        return 'upload'

    def _handle_remote_only_file(self, rel_path: str, state_entry: Dict) -> str:
        """File exists only remotely: always ``'skip'`` (await manual download or respect deletion)."""
        if not state_entry:
            logger.debug(f"{rel_path} is new on OneDrive, awaiting user download")
            return 'skip'

        if state_entry.get('downloaded') or state_entry.get('eTag'):
            logger.info(f"{rel_path} was deleted locally, keeping deleted")
            return 'skip'

        return 'skip'

    def _handle_file_exists_both(
        self, rel_path: str, local_info: Dict, remote_info: Dict, state_entry: Dict
    ) -> str:
        """File exists on both sides: decide between upload/download/conflict/skip."""
        if not state_entry:
            return self._handle_untracked_file(rel_path, local_info, remote_info)

        if not state_entry.get('downloaded'):
            logger.debug(f"{rel_path} not marked as downloaded, skipping sync")
            return 'skip'

        local_changed = self._is_local_modified(local_info, state_entry)
        remote_changed = self._is_remote_modified(remote_info, state_entry)

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
        """File exists both places but has no sync state: ``'skip'`` if same size else ``'conflict'``."""
        if local_info['size'] == remote_info['size']:
            logger.info(f"{rel_path} exists both places with same size, assuming synced")
            return 'skip'
        return 'conflict'

    # ------------------------------------------------------------------ #
    # Change detection                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_local_modified(local_info: Dict, state_entry: Dict) -> bool:
        return (state_entry.get('mtime', 0) != local_info['mtime'] or
                state_entry.get('size', 0) != local_info['size'])

    @staticmethod
    def _is_remote_modified(remote_info: Dict, state_entry: Dict) -> bool:
        return (state_entry.get('eTag', '') != remote_info['eTag'] or
                state_entry.get('remote_modified', '') != remote_info['lastModifiedDateTime'])
