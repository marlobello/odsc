"""JSON-based state storage backend (legacy)."""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from .base import StateBackend

logger = logging.getLogger(__name__)


class JsonStateBackend(StateBackend):
    """JSON file-based state storage.
    
    This is the original storage format. It loads the entire state
    into memory and writes it back on every save.
    
    Performance characteristics:
    - Load: O(n) - must parse entire JSON file
    - Save: O(n) - must serialize entire state
    - Lookup: O(1) - dict lookup in memory
    - Memory: O(n) - entire state in memory
    """
    
    def __init__(self, state_file: Path):
        """Initialize JSON backend.
        
        Args:
            state_file: Path to JSON state file
        """
        self.state_file = state_file
        self._state: Optional[Dict[str, Any]] = None
    
    def load(self) -> Dict[str, Any]:
        """Load complete state from JSON file."""
        if self._state is not None:
            return self._state
        
        if not self.state_file.exists():
            self._state = self._get_default_state()
            return self._state
        
        try:
            self._state = json.loads(self.state_file.read_text())
            # Ensure required keys exist
            if 'file_cache' not in self._state:
                self._state['file_cache'] = {}
            if 'files' not in self._state:
                self._state['files'] = {}
            return self._state
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load state from {self.state_file}: {e}")
            self._state = self._get_default_state()
            return self._state
    
    def save(self, state: Dict[str, Any]) -> None:
        """Save complete state to JSON file."""
        self._state = state
        try:
            # Ensure parent directory exists
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Write atomically: write to temp file, then rename
            temp_file = self.state_file.with_suffix('.json.tmp')
            temp_file.write_text(json.dumps(state, indent=2))
            temp_file.replace(self.state_file)
            
            # Set secure permissions
            self.state_file.chmod(0o600)
        except OSError as e:
            logger.error(f"Failed to save state to {self.state_file}: {e}")
            raise
    
    def get_file_cache(self, path: str) -> Optional[Dict]:
        """Get single file's cache entry."""
        state = self.load()
        return state.get('file_cache', {}).get(path)
    
    def set_file_cache(self, path: str, data: Dict) -> None:
        """Update or insert file cache entry."""
        state = self.load()
        if 'file_cache' not in state:
            state['file_cache'] = {}
        state['file_cache'][path] = data
        # Note: Changes are in memory, must call save() to persist
    
    def delete_file_cache(self, path: str) -> None:
        """Remove file from cache."""
        state = self.load()
        if 'file_cache' in state and path in state['file_cache']:
            del state['file_cache'][path]
        # Note: Changes are in memory, must call save() to persist
    
    def get_all_file_cache(self) -> Dict[str, Dict]:
        """Get all cached files."""
        state = self.load()
        return state.get('file_cache', {})
    
    def get_sync_state(self, path: str) -> Optional[Dict]:
        """Get sync tracking state for a file."""
        state = self.load()
        return state.get('files', {}).get(path)
    
    def set_sync_state(self, path: str, data: Dict) -> None:
        """Update or insert sync state."""
        state = self.load()
        if 'files' not in state:
            state['files'] = {}
        state['files'][path] = data
        # Note: Changes are in memory, must call save() to persist
    
    def get_all_sync_state(self) -> Dict[str, Dict]:
        """Get all sync state entries."""
        state = self.load()
        return state.get('files', {})
    
    def get_metadata(self, key: str) -> Optional[str]:
        """Get metadata value."""
        state = self.load()
        value = state.get(key)
        return str(value) if value is not None else None
    
    def set_metadata(self, key: str, value: str) -> None:
        """Set metadata value."""
        state = self.load()
        state[key] = value
        # Note: Changes are in memory, must call save() to persist
    
    def close(self) -> None:
        """Close backend (no-op for JSON)."""
        pass
    
    @staticmethod
    def _get_default_state() -> Dict[str, Any]:
        """Get default empty state structure."""
        return {
            'file_cache': {},
            'files': {},
            'delta_token': '',
            'last_sync': ''
        }
