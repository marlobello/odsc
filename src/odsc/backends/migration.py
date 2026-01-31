"""Migration utilities for state backends."""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from .json_backend import JsonStateBackend
from .sqlite_backend import SqliteStateBackend

logger = logging.getLogger(__name__)


def migrate_json_to_sqlite(json_path: Path, sqlite_path: Path) -> bool:
    """Migrate JSON state file to SQLite database.
    
    This is a one-way migration that:
    1. Backs up the JSON file
    2. Creates SQLite database
    3. Migrates all data
    4. Verifies migration success
    5. Marks migration complete
    
    Args:
        json_path: Path to existing JSON state file
        sqlite_path: Path for new SQLite database
        
    Returns:
        True if migration successful, False otherwise
    """
    if not json_path.exists():
        logger.warning(f"JSON file does not exist: {json_path}")
        return False
    
    if sqlite_path.exists():
        logger.warning(f"SQLite database already exists: {sqlite_path}")
        return False
    
    logger.info(f"Starting migration from JSON to SQLite...")
    logger.info(f"Source: {json_path}")
    logger.info(f"Target: {sqlite_path}")
    
    try:
        # Step 1: Backup JSON file
        backup_path = json_path.with_suffix('.json.backup')
        logger.info(f"Creating backup: {backup_path}")
        shutil.copy2(json_path, backup_path)
        
        # Step 2: Load JSON data
        logger.info("Loading JSON state...")
        json_backend = JsonStateBackend(json_path)
        state = json_backend.load()
        
        file_cache_count = len(state.get('file_cache', {}))
        sync_state_count = len(state.get('files', {}))
        logger.info(f"Loaded {file_cache_count} file cache entries, "
                   f"{sync_state_count} sync state entries")
        
        # Step 3: Create SQLite database
        logger.info("Creating SQLite database...")
        sqlite_backend = SqliteStateBackend(sqlite_path)
        
        # Step 4: Migrate data in batches for better performance
        logger.info("Migrating file_cache...")
        file_cache = state.get('file_cache', {})
        if file_cache:
            # Use batch insert for performance
            sqlite_backend._batch_insert_cache(file_cache)
        
        logger.info("Migrating sync_state...")
        sync_state = state.get('files', {})
        if sync_state:
            # Use batch insert for performance
            sqlite_backend._batch_insert_sync_state(sync_state)
        
        logger.info("Migrating metadata...")
        sqlite_backend.set_metadata('delta_token', state.get('delta_token', ''))
        sqlite_backend.set_metadata('last_sync', state.get('last_sync', ''))
        
        # Step 5: Mark migration complete
        sqlite_backend.set_metadata('migrated_from_json', 'true')
        sqlite_backend.set_metadata('migration_date', datetime.now().isoformat())
        sqlite_backend.set_metadata('source_file', str(json_path))
        
        # Step 6: Verify migration
        logger.info("Verifying migration...")
        migrated_state = sqlite_backend.load()
        
        migrated_cache = len(migrated_state.get('file_cache', {}))
        migrated_sync = len(migrated_state.get('files', {}))
        
        if migrated_cache != file_cache_count:
            raise ValueError(f"File cache mismatch: {migrated_cache} != {file_cache_count}")
        
        if migrated_sync != sync_state_count:
            raise ValueError(f"Sync state mismatch: {migrated_sync} != {sync_state_count}")
        
        logger.info("✅ Migration verification passed!")
        logger.info(f"✅ Migrated {migrated_cache} cache entries and {migrated_sync} sync entries")
        
        # Close connections
        json_backend.close()
        sqlite_backend.close()
        
        logger.info("✅ Migration completed successfully!")
        logger.info(f"JSON backup saved at: {backup_path}")
        logger.info(f"SQLite database created at: {sqlite_path}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        
        # Cleanup on failure
        if sqlite_path.exists():
            try:
                sqlite_path.unlink()
                logger.info("Cleaned up partial SQLite database")
            except OSError:
                pass
        
        return False


def get_state_file_size(path: Path) -> str:
    """Get human-readable file size.
    
    Args:
        path: File path
        
    Returns:
        String like "44.5 MB"
    """
    if not path.exists():
        return "0 B"
    
    size = path.stat().st_size
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    
    return f"{size:.1f} TB"


def compare_backend_sizes(json_path: Path, sqlite_path: Path) -> None:
    """Compare file sizes of JSON and SQLite backends.
    
    Args:
        json_path: Path to JSON file
        sqlite_path: Path to SQLite database
    """
    json_size = get_state_file_size(json_path)
    sqlite_size = get_state_file_size(sqlite_path)
    
    logger.info("Storage comparison:")
    logger.info(f"  JSON:   {json_size}")
    logger.info(f"  SQLite: {sqlite_size}")
    
    if json_path.exists() and sqlite_path.exists():
        ratio = json_path.stat().st_size / sqlite_path.stat().st_size
        logger.info(f"  Ratio:  {ratio:.1f}x smaller with SQLite")
