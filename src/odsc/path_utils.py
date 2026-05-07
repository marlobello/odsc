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

    if any(sep in name for sep in ('/', '\\')) or '\x00' in name:
        raise SecurityError(f"Invalid OneDrive item name contains path separator or null byte: {name!r}")
    
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
    
    # Normalize separators before inspecting components without collapsing dot segments
    parts = path.replace('\\', '/').split('/')

    safe_parts = []
    for part in parts:
        if part in ('..', '.'):
            raise SecurityError(f"Invalid OneDrive path contains forbidden component {part!r}: {raw_path!r}")
        if part in ('/', '\\', ''):
            raise SecurityError(f"Invalid OneDrive path contains empty or separator-only component: {raw_path!r}")
        if part.startswith('/') or part.startswith('\\'):
            raise SecurityError(f"Invalid OneDrive path contains absolute component {part!r}: {raw_path!r}")
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
    sync_dir_resolved = sync_dir.resolve()
    sync_dir_abs = sync_dir if sync_dir.is_absolute() else (Path.cwd() / sync_dir)
    full_path = sync_dir_abs / rel_path

    if Path(rel_path).is_absolute():
        raise SecurityError(f"Absolute paths are not allowed for sync operations: {rel_path}")

    # Check for symlinks before resolving the path.
    check_path = full_path
    while check_path != sync_dir_abs:
        if check_path.is_symlink():
            raise SecurityError(f"Symlink detected in sync path before resolution: {rel_path}")
        if check_path == check_path.parent:
            raise SecurityError(f"Path validation failed before reaching sync root: {rel_path}")
        check_path = check_path.parent

    resolved_path = full_path.resolve()
    
    # Check it's within sync directory
    try:
        resolved_path.relative_to(sync_dir_resolved)
    except ValueError:
        raise SecurityError(f"Resolved path escapes sync directory: {rel_path}")

    return resolved_path


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
            try:
                parent.resolve().relative_to(sync_dir_resolved)
            except ValueError as e:
                raise SecurityError(
                    f"Refusing to clean up directory outside sync root: {parent}"
                ) from e

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
