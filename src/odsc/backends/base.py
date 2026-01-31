"""Abstract base class for state storage backends."""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class StateBackend(ABC):
    """Abstract base class for state storage backends.
    
    Provides interface for storing and retrieving sync state, including:
    - File cache (OneDrive metadata for all files)
    - Sync state (per-file tracking of local/remote state)
    - Metadata (delta tokens, last sync time, etc.)
    """
    
    @abstractmethod
    def load(self) -> Dict[str, Any]:
        """Load complete state as a dictionary.
        
        Returns:
            Dict with keys: 'file_cache', 'files', 'delta_token', 'last_sync'
        """
        pass
    
    @abstractmethod
    def save(self, state: Dict[str, Any]) -> None:
        """Save complete state from dictionary.
        
        Args:
            state: Dict with keys: 'file_cache', 'files', 'delta_token', 'last_sync'
        """
        pass
    
    @abstractmethod
    def get_file_cache(self, path: str) -> Optional[Dict]:
        """Get single file's cache entry.
        
        Args:
            path: Relative file path
            
        Returns:
            Dict with OneDrive metadata, or None if not found
        """
        pass
    
    @abstractmethod
    def set_file_cache(self, path: str, data: Dict) -> None:
        """Update or insert file cache entry.
        
        Args:
            path: Relative file path
            data: OneDrive metadata dict
        """
        pass
    
    @abstractmethod
    def delete_file_cache(self, path: str) -> None:
        """Remove file from cache.
        
        Args:
            path: Relative file path
        """
        pass
    
    @abstractmethod
    def get_all_file_cache(self) -> Dict[str, Dict]:
        """Get all cached files.
        
        Returns:
            Dict mapping path -> metadata
        """
        pass
    
    @abstractmethod
    def get_sync_state(self, path: str) -> Optional[Dict]:
        """Get sync tracking state for a file.
        
        Args:
            path: Relative file path
            
        Returns:
            Dict with sync state, or None if not found
        """
        pass
    
    @abstractmethod
    def set_sync_state(self, path: str, data: Dict) -> None:
        """Update or insert sync state.
        
        Args:
            path: Relative file path
            data: Sync state dict
        """
        pass
    
    @abstractmethod
    def get_all_sync_state(self) -> Dict[str, Dict]:
        """Get all sync state entries.
        
        Returns:
            Dict mapping path -> sync state
        """
        pass
    
    @abstractmethod
    def get_metadata(self, key: str) -> Optional[str]:
        """Get metadata value.
        
        Args:
            key: Metadata key (e.g., 'delta_token', 'last_sync')
            
        Returns:
            Value string, or None if not found
        """
        pass
    
    @abstractmethod
    def set_metadata(self, key: str, value: str) -> None:
        """Set metadata value.
        
        Args:
            key: Metadata key
            value: Value string
        """
        pass
    
    @abstractmethod
    def close(self) -> None:
        """Close backend and release resources."""
        pass
