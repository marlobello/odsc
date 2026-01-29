#!/usr/bin/env python3
"""Configuration management for ODSC."""

import json
import logging
import base64
from pathlib import Path
from typing import Optional, Dict, Any

from cryptography.fernet import Fernet, InvalidToken
import keyring

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
        validated_value = self._validate_config_value(key, value)
        self._config[key] = validated_value
        self.save()
    
    def _validate_config_value(self, key: str, value: Any) -> Any:
        """Validate and sanitize configuration value.
        
        Args:
            key: Configuration key
            value: Value to validate
            
        Returns:
            Validated/sanitized value
            
        Raises:
            ValueError: If value is invalid
        """
        if key == 'sync_interval':
            # Must be integer between 60 seconds (1 min) and 86400 seconds (1 day)
            try:
                interval = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"sync_interval must be an integer, got: {value}")
            
            if interval < 60:
                raise ValueError(f"sync_interval must be at least 60 seconds (1 minute), got: {interval}")
            if interval > 86400:
                raise ValueError(f"sync_interval must be at most 86400 seconds (1 day), got: {interval}")
            
            return interval
        
        elif key == 'sync_directory':
            # Must be valid path, parent must exist or be creatable
            try:
                path = Path(value).expanduser().resolve()
            except Exception as e:
                raise ValueError(f"Invalid path for sync_directory: {e}")
            
            # Check parent directory exists
            if not path.parent.exists():
                raise ValueError(f"Parent directory does not exist: {path.parent}")
            
            # Ensure we have write permissions to parent
            if not path.exists():
                # Check parent is writable
                if not path.parent.is_dir():
                    raise ValueError(f"Parent is not a directory: {path.parent}")
                # Try to verify write access (not foolproof but catches common issues)
                import os
                if not os.access(path.parent, os.W_OK):
                    raise ValueError(f"No write permission to parent directory: {path.parent}")
            else:
                # Directory exists, check it's actually a directory
                if not path.is_dir():
                    raise ValueError(f"sync_directory exists but is not a directory: {path}")
            
            return str(path)
        
        elif key == 'log_level':
            # Must be valid logging level
            valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
            level = str(value).upper()
            
            if level not in valid_levels:
                raise ValueError(f"log_level must be one of {valid_levels}, got: {value}")
            
            return level
        
        elif key == 'client_id':
            # Basic format validation - should be UUID-like or empty
            client_id = str(value).strip()
            
            if client_id:
                # Check it looks like a UUID (loose validation)
                if len(client_id) < 32 or len(client_id) > 40:
                    raise ValueError(f"client_id appears invalid (wrong length): {client_id}")
                
                # Check for dangerous characters
                import re
                if not re.match(r'^[a-f0-9\-]+$', client_id, re.IGNORECASE):
                    raise ValueError(f"client_id contains invalid characters: {client_id}")
            
            return client_id
        
        elif key == 'auto_start':
            # Must be boolean
            if isinstance(value, bool):
                return value
            
            # Try to convert string to boolean
            if isinstance(value, str):
                if value.lower() in ('true', '1', 'yes', 'on'):
                    return True
                elif value.lower() in ('false', '0', 'no', 'off'):
                    return False
            
            raise ValueError(f"auto_start must be boolean, got: {value}")
        
        # For unknown keys, just store as-is (allow extensibility)
        return value
    
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
        """Save sync state.
        
        Args:
            state_data: State data dictionary
        """
        with open(self.state_path, 'w') as f:
            json.dump(state_data, f, indent=2)
        # Secure file permissions (owner read/write only)
        self.state_path.chmod(0o600)
    
    def load_state(self) -> Dict[str, Any]:
        """Load sync state.
        
        Returns:
            State data dictionary
        """
        if self.state_path.exists():
            with open(self.state_path, 'r') as f:
                return json.load(f)
        return {
            'files': {}, 
            'last_sync': None,
            'delta_token': None,  # For incremental OneDrive sync
            'file_cache': {},  # Cache of remote file metadata
        }
