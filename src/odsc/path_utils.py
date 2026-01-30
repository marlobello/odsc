#!/usr/bin/env python3
"""Path utilities for secure path handling in ODSC."""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Raised when a security violation is detected."""
    pass


def extract_item_path(item: Dict[str, Any]) -> str:
    """Extract and sanitize full path from OneDrive item.
    
    Args:
        item: OneDrive item dictionary with 'name' and 'parentReference'
        
    Returns:
        Sanitized relative path (e.g., "Documents/file.txt")
    """
    parent_path = item.get('parentReference', {}).get('path', '')
    name = item.get('name', '')
    
    if parent_path:
        safe_parent = sanitize_onedrive_path(parent_path)
        return str(Path(safe_parent) / name) if safe_parent else name
    return name


def sanitize_onedrive_path(raw_path: str) -> str:
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
        if part in ('..', '.', '/', '\\', ''):
            logger.warning(f"Blocked dangerous path component: {part}")
            continue
        # Block absolute paths
        if part.startswith('/') or part.startswith('\\'):
            logger.warning(f"Blocked absolute path component: {part}")
            continue
        safe_parts.append(part)
    
    if not safe_parts:
        return ''
    
    return str(Path(*safe_parts))


def validate_sync_path(rel_path: str, sync_dir: Path) -> Path:
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
            # Reached root without finding sync_dir - security issue
            raise SecurityError(f"Path validation failed - reached filesystem root: {rel_path}")
        check_path = check_path.parent
    
    return full_path


def cleanup_empty_parent_dirs(file_path: Path, sync_dir: Path) -> None:
    """Remove empty parent directories up to sync_dir.
    
    Args:
        file_path: Path to the deleted file
        sync_dir: Sync directory (don't delete above this)
    """
    try:
        parent = file_path.parent
        sync_dir_resolved = sync_dir.resolve()
        
        while parent != sync_dir_resolved and parent.exists():
            # Check if directory is empty
            if not any(parent.iterdir()):
                logger.info(f"Removing empty directory: {parent.relative_to(sync_dir_resolved)}")
                parent.rmdir()
                parent = parent.parent
            else:
                # Directory not empty, stop
                break
    except (OSError, ValueError) as e:
        # Permission error or other OS issue - log but don't fail
        logger.debug(f"Could not clean up empty directories: {e}")
