"""Shared file caching service for delta query processing.

This service provides unified delta change processing logic shared between
the GUI and sync daemon to eliminate code duplication.
"""

import logging
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

from ..path_utils import sanitize_onedrive_path

logger = logging.getLogger(__name__)


class FileCacheService:
    """Service for managing OneDrive file cache with delta queries."""
    
    @staticmethod
    def process_delta_changes(
        changes: List[Dict[str, Any]],
        existing_cache: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Process delta changes and update file cache.
        
        Args:
            changes: List of change items from OneDrive delta query
            existing_cache: Existing file cache dictionary
            
        Returns:
            Updated file cache dictionary
        """
        file_cache = dict(existing_cache)  # Make a copy
        
        for item in changes:
            if item.get('deleted'):
                # Handle deleted items
                item_id = item['id']
                for path in list(file_cache.keys()):
                    if file_cache[path].get('id') == item_id:
                        del file_cache[path]
                        logger.debug(f"Removed deleted item from cache: {path}")
                        break
            else:
                # Handle added/modified items
                try:
                    full_path = FileCacheService._build_item_path(item)
                    file_cache[full_path] = item
                    logger.debug(f"Updated cache for: {full_path}")
                except Exception as e:
                    logger.warning(f"Error processing change: {e}")
        
        return file_cache
    
    @staticmethod
    def build_initial_cache(
        changes: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Build initial file cache from full delta query.
        
        Args:
            changes: List of all items from OneDrive
            
        Returns:
            File cache dictionary
        """
        file_cache = {}
        
        for item in changes:
            if not item.get('deleted'):
                try:
                    full_path = FileCacheService._build_item_path(item)
                    file_cache[full_path] = item
                except Exception as e:
                    logger.warning(f"Error processing item: {e}")
        
        return file_cache
    
    @staticmethod
    def cache_to_file_list(
        cache: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert file cache to list format with path metadata.
        
        Args:
            cache: File cache dictionary
            
        Returns:
            List of file items with _cache_path added
        """
        files = []
        for path, item in cache.items():
            # Ensure name field exists
            if 'name' not in item and path:
                item = dict(item)
                item['name'] = Path(path).name
                item['_cache_path'] = path
            files.append(item)
        return files
    
    @staticmethod
    def _build_item_path(item: Dict[str, Any]) -> str:
        """Build full path for an item from its metadata.
        
        Args:
            item: OneDrive item metadata
            
        Returns:
            Full path string
        """
        parent_ref = item.get('parentReference', {})
        parent_path = parent_ref.get('path', '')
        name = item.get('name', '')
        
        if parent_path:
            safe_parent = sanitize_onedrive_path(parent_path)
            full_path = str(Path(safe_parent) / name) if safe_parent else name
        else:
            full_path = name
        
        return full_path
