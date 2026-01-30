#!/usr/bin/env python3
"""Configuration and state management for ODSC.

State File Structure
====================

sync_state.json contains:

{
  "files": {
    // Files actively being synced (downloaded by user or uploaded locally)
    "Documents/file.txt": {
      "mtime": 1234567890,          // Local modification time (Unix timestamp)
      "size": 1024,                 // File size in bytes
      "eTag": "abc123",             // OneDrive eTag for change detection
      "remote_modified": "2024-...", // OneDrive lastModifiedDateTime (ISO 8601)
      "downloaded": true,           // User explicitly downloaded this file
      "upload_error": null          // Last upload error (or null if successful)
    }
  },
  
  "file_cache": {
    // Complete OneDrive tree (all files and folders from delta query)
    // Used to detect what exists remotely vs locally
    "Documents/file.txt": {
      "id": "ABC123",               // OneDrive item ID
      "size": 1024,
      "eTag": "abc123",
      "lastModifiedDateTime": "2024-...",
      "is_folder": false
    },
    "Documents/FolderName": {
      "id": "XYZ789",
      "folder": {},                 // Present if item is a folder
      "is_folder": true
    }
  },
  
  "delta_token": "https://...",     // OneDrive delta query continuation token
  "last_sync": "2024-01-30T12:00:00" // Last successful sync timestamp (ISO 8601)
}

Key Distinction:
- files: What we're ACTIVELY syncing (subset of file_cache, only items with downloaded=True)
- file_cache: What EXISTS on OneDrive (complete tree, all files and folders)

This separation allows:
1. Selective sync (only download files user wants)
2. Detection of remote deletions (in file_cache but not in latest delta)
3. Tracking sync history per file (mtime, eTag for change detection)
"""

import json
import logging
import base64
import fcntl
from pathlib import Path
from typing import Optional, Dict, Any

from cryptography.fernet import Fernet, InvalidToken
import keyring

from .validators import validate_config_value, ValidationError

logger = logging.getLogger(__name__)


class Config:
    """Manages ODSC configuration."""
    
    DEFAULT_CONFIG_DIR = Path.home() / ".config" / "odsc"
    DEFAULT_SYNC_DIR = Path.home() / "OneDrive"
    CONFIG_FILE = "config.json"
    TOKEN_FILE = ".onedrive_token"
    STATE_FILE = "sync_state.json"
    LOG_FILE = "odsc.log"
    FORCE_SYNC_FILE = ".force_sync"
    
    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize configuration manager.
        
        Args:
            config_dir: Custom configuration directory path
        """
        self.config_dir = config_dir or self.DEFAULT_CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        self.config_path = self.config_dir / self.CONFIG_FILE
        self.token_path = self.config_dir / self.TOKEN_FILE
        self.state_path = self.config_dir / self.STATE_FILE
        self.log_path = self.config_dir / self.LOG_FILE
        self.force_sync_path = self.config_dir / self.FORCE_SYNC_FILE
        
        self._config: Dict[str, Any] = {}
        self.load()
    
    def load(self) -> None:
        """Load configuration from file."""
        if self.config_path.exists():
            with open(self.config_path, 'r') as f:
                self._config = json.load(f)
            logger.debug(f"Loaded config from {self.config_path}")
        else:
            # Initialize with defaults
            self._config = {
                'sync_directory': str(self.DEFAULT_SYNC_DIR),
                'sync_interval': 300,  # 5 minutes
                'auto_start': False,
                'log_level': 'INFO',
            }
            self.save()
            logger.debug(f"Created default config at {self.config_path}")
    
    def save(self) -> None:
        """Save configuration to file."""
        with open(self.config_path, 'w') as f:
            json.dump(self._config, f, indent=2)
        # Secure file permissions (owner read/write only)
        self.config_path.chmod(0o600)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value.
        
        Args:
            key: Configuration key
            default: Default value if key not found
            
        Returns:
            Configuration value
        """
        return self._config.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set configuration value with validation.
        
        Args:
            key: Configuration key
            value: Configuration value
            
        Raises:
            ValueError: If value is invalid for the given key
        """
        # Validate value based on key
        try:
            validated_value = validate_config_value(key, value)
        except ValidationError as e:
            # Convert ValidationError to ValueError for backward compatibility
            raise ValueError(str(e))
        self._config[key] = validated_value
        self.save()
    

    @property
    def sync_directory(self) -> Path:
        """Get sync directory path."""
        return Path(self._config['sync_directory'])
    
    @sync_directory.setter
    def sync_directory(self, path: Path) -> None:
        """Set sync directory path."""
        self._config['sync_directory'] = str(path)
        self.save()
    
    @property
    def sync_interval(self) -> int:
        """Get sync interval in seconds."""
        return self._config.get('sync_interval', 300)
    
    @property
    def client_id(self) -> str:
        """Get OneDrive client ID."""
        return self._config.get('client_id', '')
    
    @property
    def log_level(self) -> str:
        """Get log level."""
        return self._config.get('log_level', 'INFO').upper()
    
    def _get_encryption_key(self) -> bytes:
        """Get or create encryption key from system keyring.
        
        Returns:
            Encryption key bytes
        """
        service_name = "odsc"
        key_name = "token_encryption_key"
        
        # Try to get existing key
        key_str = keyring.get_password(service_name, key_name)
        
        if key_str:
            # Decode existing key
            return base64.b64decode(key_str.encode())
        
        # Generate new key
        key = Fernet.generate_key()
        
        # Store in keyring
        key_str = base64.b64encode(key).decode()
        keyring.set_password(service_name, key_name, key_str)
        
        logger.info("Generated new encryption key")
        return key
    
    def _encrypt_token(self, token_data: Dict[str, Any]) -> bytes:
        """Encrypt token data.
        
        Args:
            token_data: Token data dictionary
            
        Returns:
            Encrypted token bytes
        """
        key = self._get_encryption_key()
        fernet = Fernet(key)
        
        # Serialize and encrypt
        json_str = json.dumps(token_data)
        encrypted = fernet.encrypt(json_str.encode())
        
        return encrypted
    
    def _decrypt_token(self, encrypted_data: bytes) -> Dict[str, Any]:
        """Decrypt token data.
        
        Args:
            encrypted_data: Encrypted token bytes
            
        Returns:
            Decrypted token data dictionary
            
        Raises:
            ValueError: If decryption fails
        """
        try:
            key = self._get_encryption_key()
            fernet = Fernet(key)
            
            # Decrypt and deserialize
            decrypted = fernet.decrypt(encrypted_data)
            token_data = json.loads(decrypted.decode())
            
            return token_data
        except InvalidToken:
            raise ValueError("Invalid or corrupted token data")
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")
    
    def save_token(self, token_data: Dict[str, Any]) -> None:
        """Save encrypted OneDrive authentication token.
        
        Args:
            token_data: Token data dictionary
        """
        # Encrypt token data
        encrypted = self._encrypt_token(token_data)
        
        # Write encrypted data
        self.token_path.write_bytes(encrypted)
        
        # Secure file permissions (owner read/write only)
        self.token_path.chmod(0o600)
        
        logger.info("Token saved with encryption")
    
    def load_token(self) -> Optional[Dict[str, Any]]:
        """Load and decrypt OneDrive authentication token.
        
        Returns:
            Token data or None if not found
        """
        if not self.token_path.exists():
            return None
        
        try:
            # Read encrypted data
            encrypted_data = self.token_path.read_bytes()
            
            # Try to decrypt
            token_data = self._decrypt_token(encrypted_data)
            logger.info("Token loaded and decrypted successfully")
            return token_data
            
        except ValueError as e:
            # Decryption failed - likely old plaintext token or corrupted
            logger.warning(f"Could not decrypt token: {e}")
            logger.warning("Token file may be from old version - please re-authenticate")
            # Delete invalid token
            self.token_path.unlink(missing_ok=True)
            return None
        except Exception as e:
            logger.error(f"Error loading token: {e}")
            return None
    
    def save_state(self, state_data: Dict[str, Any]) -> None:
        """Save sync state with file locking to prevent corruption.
        
        Args:
            state_data: State data dictionary
        """
        # Use exclusive lock to prevent concurrent writes
        with open(self.state_path, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(state_data, f, indent=2)
                f.flush()  # Ensure data is written
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        # Secure file permissions (owner read/write only)
        self.state_path.chmod(0o600)
    
    def load_state(self) -> Dict[str, Any]:
        """Load sync state with file locking.
        
        Returns:
            State data dictionary
        """
        if self.state_path.exists():
            try:
                with open(self.state_path, 'r') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    try:
                        return json.load(f)
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except json.JSONDecodeError as e:
                logger.error(f"State file corrupted, resetting: {e}")
                # Return default state if file is corrupted
                return {
                    'files': {}, 
                    'last_sync': None,
                    'delta_token': None,
                    'file_cache': {},
                }
        
        return {
            'files': {}, 
            'last_sync': None,
            'delta_token': None,  # For incremental OneDrive sync
            'file_cache': {},  # Cache of remote file metadata
        }
