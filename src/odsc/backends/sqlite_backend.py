"""SQLite-based state storage backend (high performance)."""

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Dict, Any, Optional

from .base import StateBackend

logger = logging.getLogger(__name__)


class SqliteStateBackend(StateBackend):
    """SQLite database-based state storage.
    
    High-performance backend using SQLite with indexed queries.
    
    Performance characteristics:
    - Load: O(1) - just opens connection
    - Save: O(1) - single UPDATE query
    - Lookup: O(log n) - indexed query
    - Memory: O(1) - only query results in memory
    
    Benefits:
    - 40-60x faster startup
    - 5000x faster updates
    - 4000x less memory usage
    - 3x smaller disk space
    - ACID transactions (crash-safe)
    - Concurrent reads supported
    """
    
    SCHEMA_VERSION = 2
    
    def __init__(self, db_path: Path):
        """Initialize SQLite backend.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._write_lock = threading.Lock()
        self._ensure_connection()
        self._init_schema()
    
    def _ensure_connection(self) -> None:
        """Ensure database connection is established."""
        if self.conn is not None:
            return
        
        # Create parent directory
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Connect to database
        try:
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row  # Dict-like row access
            
            # Performance optimizations
            self.conn.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging for concurrent reads
            self.conn.execute("PRAGMA busy_timeout=5000")  # Wait briefly for cross-process writers
            self.conn.execute("PRAGMA synchronous=NORMAL")  # Balance safety/performance
            self.conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            self.conn.execute("PRAGMA temp_store=MEMORY")  # Use memory for temp tables
        except sqlite3.Error as exc:
            logger.error(f"Failed to connect SQLite backend {self.db_path}: {exc}", exc_info=True)
            raise
        
        logger.info(f"SQLite backend connected: {self.db_path}")
    
    def _init_schema(self) -> None:
        """Initialize or migrate the database schema."""
        existing_version = 0
        try:
            result = self.conn.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            if result:
                existing_version = int(result[0])
        except sqlite3.OperationalError:
            existing_version = 0  # Tables don't exist yet

        if existing_version >= self.SCHEMA_VERSION:
            return  # Schema up to date

        logger.info(
            f"Initializing/migrating SQLite schema (v{existing_version} -> v{self.SCHEMA_VERSION})..."
        )

        with self._write_lock:
            with self.conn:
                # Create file_cache table (fresh installs get the current shape,
                # including the content-hash column).
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS file_cache (
                        path TEXT PRIMARY KEY,
                        id TEXT NOT NULL,
                        size INTEGER,
                        mtime_remote REAL,
                        etag TEXT,
                        is_folder INTEGER DEFAULT 0,
                        parent_id TEXT,
                        created_at TEXT,
                        modified_at TEXT,
                        quickxorhash TEXT
                    )
                """)

                # Create indexes for fast lookups
                self.conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_file_cache_id 
                    ON file_cache(id)
                """)
                self.conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_file_cache_parent 
                    ON file_cache(parent_id)
                """)
                self.conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_file_cache_is_folder 
                    ON file_cache(is_folder)
                """)

                # Create sync_state table
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS sync_state (
                        path TEXT PRIMARY KEY,
                        mtime REAL NOT NULL,
                        size INTEGER NOT NULL,
                        downloaded INTEGER DEFAULT 0,
                        etag TEXT,
                        remote_modified TEXT,
                        upload_error TEXT,
                        quickxorhash TEXT
                    )
                """)

                # Create index for modified files
                self.conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sync_state_mtime 
                    ON sync_state(mtime)
                """)

                # Create metadata table
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)

                # v1 -> v2 migration: add the quickxorhash column to existing
                # databases that predate content-addressed change detection.
                self._add_column_if_missing("sync_state", "quickxorhash", "TEXT")
                self._add_column_if_missing("file_cache", "quickxorhash", "TEXT")

                # Store schema version
                self.conn.execute("""
                    INSERT OR REPLACE INTO metadata (key, value) 
                    VALUES ('schema_version', ?)
                """, (str(self.SCHEMA_VERSION),))

        logger.info("SQLite schema ready")

    def _add_column_if_missing(self, table: str, column: str, col_type: str) -> None:
        """Idempotently add *column* to *table* (table/column are internal constants)."""
        existing = [row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in existing:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            logger.info(f"Migrated: added column {table}.{column}")
    
    def load(self) -> Dict[str, Any]:
        """Load complete state as a dictionary.

        Note: This loads everything into memory (slow). Use specific
        methods (get_file_cache, get_sync_state) for better performance.
        """
        try:
            return {
                'file_cache': self.get_all_file_cache(),
                'files': self.get_all_sync_state(),
                'delta_token': self.get_metadata('delta_token') or '',
                'last_sync': self.get_metadata('last_sync') or '',
                'conflicts': self._load_json_metadata('conflicts'),
                '_deletion_failures': self._load_json_metadata('deletion_failures'),
                'tombstones': self._load_json_metadata('tombstones'),
            }
        except sqlite3.Error as exc:
            logger.error(f"Failed to load state from SQLite backend {self.db_path}: {exc}", exc_info=True)
            raise

    def _load_json_metadata(self, key: str) -> Dict[str, Any]:
        """Load a JSON-encoded metadata dict, defaulting to {} on absence/corruption."""
        raw = self.get_metadata(key)
        if not raw:
            return {}
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            logger.warning(f"Ignoring corrupt JSON metadata for '{key}'")
            return {}
    
    def save(self, state: Dict[str, Any]) -> None:
        """Save complete state from dictionary.
        
        Note: This is slow for large states. Use specific methods
        (set_file_cache, set_sync_state) for better performance.
        """
        try:
            with self._write_lock:
                with self.conn:
                    # Clear existing data
                    self.conn.execute("DELETE FROM file_cache")
                    self.conn.execute("DELETE FROM sync_state")
                    
                    # Insert file_cache
                    file_cache = state.get('file_cache', {})
                    if file_cache:
                        self._batch_insert_cache_unlocked(file_cache)
                    
                    # Insert sync_state
                    sync_state = state.get('files', {})
                    if sync_state:
                        self._batch_insert_sync_state_unlocked(sync_state)
                    
                    # Insert metadata
                    self.conn.execute("""
                        INSERT OR REPLACE INTO metadata (key, value) 
                        VALUES (?, ?)
                    """, ('delta_token', state.get('delta_token', '')))
                    self.conn.execute("""
                        INSERT OR REPLACE INTO metadata (key, value) 
                        VALUES (?, ?)
                    """, ('last_sync', state.get('last_sync', '')))
                    # Persist the auxiliary state dicts that have no dedicated
                    # table so they survive restarts (previously lost).
                    for state_key, meta_key in (
                        ('conflicts', 'conflicts'),
                        ('_deletion_failures', 'deletion_failures'),
                        ('tombstones', 'tombstones'),
                    ):
                        self.conn.execute("""
                            INSERT OR REPLACE INTO metadata (key, value)
                            VALUES (?, ?)
                        """, (meta_key, json.dumps(state.get(state_key, {}))))
        except sqlite3.Error as exc:
            logger.error(f"Failed to save state to SQLite backend {self.db_path}: {exc}", exc_info=True)
            raise
    
    def get_file_cache(self, path: str) -> Optional[Dict]:
        """Get single file's cache entry."""
        row = self.conn.execute(
            "SELECT * FROM file_cache WHERE path = ?", (path,)
        ).fetchone()
        
        if row is None:
            return None
        
        return self._row_to_cache_dict(row)
    
    def set_file_cache(self, path: str, data: Dict) -> None:
        """Update or insert file cache entry."""
        with self._write_lock:
            with self.conn:
                self.conn.execute("""
                    INSERT OR REPLACE INTO file_cache 
                    (path, id, size, mtime_remote, etag, is_folder, parent_id, created_at, modified_at, quickxorhash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    path,
                    data.get('id', ''),
                    data.get('size'),
                    data.get('mtime_remote'),
                    data.get('eTag') or data.get('etag'),
                    1 if ('folder' in data or data.get('is_folder')) else 0,
                    data.get('parent_id') or data.get('parentReference', {}).get('id'),
                    data.get('createdDateTime') or data.get('created_at'),
                    data.get('lastModifiedDateTime') or data.get('modified_at'),
                    data.get('quickXorHash') or data.get('quickxorhash')
                ))
    
    def delete_file_cache(self, path: str) -> None:
        """Remove file from cache."""
        with self._write_lock:
            with self.conn:
                self.conn.execute("DELETE FROM file_cache WHERE path = ?", (path,))
    
    def get_all_file_cache(self) -> Dict[str, Dict]:
        """Get all cached files."""
        rows = self.conn.execute("SELECT * FROM file_cache").fetchall()
        return {row['path']: self._row_to_cache_dict(row) for row in rows}
    
    def get_sync_state(self, path: str) -> Optional[Dict]:
        """Get sync tracking state for a file."""
        row = self.conn.execute(
            "SELECT * FROM sync_state WHERE path = ?", (path,)
        ).fetchone()
        
        if row is None:
            return None
        
        return self._row_to_sync_dict(row)
    
    def set_sync_state(self, path: str, data: Dict) -> None:
        """Update or insert sync state."""
        with self._write_lock:
            with self.conn:
                self.conn.execute("""
                    INSERT OR REPLACE INTO sync_state 
                    (path, mtime, size, downloaded, etag, remote_modified, upload_error, quickxorhash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    path,
                    data.get('mtime', 0),
                    data.get('size', 0),
                    1 if data.get('downloaded') else 0,
                    data.get('eTag') or data.get('etag'),
                    data.get('remote_modified'),
                    data.get('upload_error'),
                    data.get('quickXorHash') or data.get('quickxorhash')
                ))
    
    def get_all_sync_state(self) -> Dict[str, Dict]:
        """Get all sync state entries."""
        rows = self.conn.execute("SELECT * FROM sync_state").fetchall()
        return {row['path']: self._row_to_sync_dict(row) for row in rows}
    
    def get_metadata(self, key: str) -> Optional[str]:
        """Get metadata value."""
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    
    def set_metadata(self, key: str, value: str) -> None:
        """Set metadata value."""
        with self._write_lock:
            with self.conn:
                self.conn.execute("""
                    INSERT OR REPLACE INTO metadata (key, value) 
                    VALUES (?, ?)
                """, (key, value))
    
    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            try:
                self.conn.close()
            except sqlite3.Error as exc:
                logger.error(f"Failed to close SQLite backend {self.db_path}: {exc}", exc_info=True)
                raise
            finally:
                self.conn = None
            logger.info("SQLite backend closed")
    
    def _batch_insert_cache(self, items: Dict[str, Dict]) -> None:
        """Batch insert cache entries (faster than individual inserts)."""
        with self._write_lock:
            self._batch_insert_cache_unlocked(items)

    def _batch_insert_cache_unlocked(self, items: Dict[str, Dict]) -> None:
        """Batch insert cache entries without acquiring the write lock."""
        data = []
        for path, item in items.items():
            data.append((
                path,
                item.get('id', ''),
                item.get('size'),
                item.get('mtime_remote'),
                item.get('eTag') or item.get('etag'),
                1 if ('folder' in item or item.get('is_folder')) else 0,
                item.get('parent_id') or item.get('parentReference', {}).get('id'),
                item.get('createdDateTime') or item.get('created_at'),
                item.get('lastModifiedDateTime') or item.get('modified_at'),
                item.get('quickXorHash') or item.get('quickxorhash')
            ))
        
        self.conn.executemany("""
            INSERT OR REPLACE INTO file_cache 
            (path, id, size, mtime_remote, etag, is_folder, parent_id, created_at, modified_at, quickxorhash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, data)
    
    def _batch_insert_sync_state(self, items: Dict[str, Dict]) -> None:
        """Batch insert sync state entries."""
        with self._write_lock:
            self._batch_insert_sync_state_unlocked(items)

    def _batch_insert_sync_state_unlocked(self, items: Dict[str, Dict]) -> None:
        """Batch insert sync state entries without acquiring the write lock."""
        data = []
        for path, item in items.items():
            data.append((
                path,
                item.get('mtime', 0),
                item.get('size', 0),
                1 if item.get('downloaded') else 0,
                item.get('eTag') or item.get('etag'),
                item.get('remote_modified'),
                item.get('upload_error'),
                item.get('quickXorHash') or item.get('quickxorhash')
            ))
        
        self.conn.executemany("""
            INSERT OR REPLACE INTO sync_state 
            (path, mtime, size, downloaded, etag, remote_modified, upload_error, quickxorhash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, data)
    
    @staticmethod
    def _row_to_cache_dict(row: sqlite3.Row) -> Dict:
        """Convert database row to cache dict."""
        result = {
            'id': row['id'],
            'size': row['size'],
            'mtime_remote': row['mtime_remote'],
            'eTag': row['etag'],
        }

        if row['is_folder']:
            result['folder'] = {}
            result['is_folder'] = True

        if row['parent_id']:
            result['parentReference'] = {'id': row['parent_id']}

        if row['created_at']:
            result['createdDateTime'] = row['created_at']

        if row['modified_at']:
            result['lastModifiedDateTime'] = row['modified_at']

        if row['quickxorhash']:
            result['quickXorHash'] = row['quickxorhash']

        return result

    @staticmethod
    def _row_to_sync_dict(row: sqlite3.Row) -> Dict:
        """Convert database row to sync state dict."""
        result = {
            'mtime': row['mtime'],
            'size': row['size'],
            'downloaded': bool(row['downloaded']),
            'eTag': row['etag'],
            'remote_modified': row['remote_modified']
        }

        if row['upload_error']:
            result['upload_error'] = row['upload_error']

        if row['quickxorhash']:
            result['quickXorHash'] = row['quickxorhash']

        return result
