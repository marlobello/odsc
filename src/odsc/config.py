#!/usr/bin/env python3
"""Configuration management for ODSC."""

import json
import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class Config:
    """Manages ODSC configuration."""
    
    DEFAULT_CONFIG_DIR = Path.home() / ".config" / "odsc"
    DEFAULT_SYNC_DIR = Path.home() / "OneDrive"
    CONFIG_FILE = "config.json"
    TOKEN_FILE = ".onedrive_token"
    STATE_FILE = "sync_state.json"
    LOG_FILE = "odsc.log"
    
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
        """Set configuration value.
        
        Args:
            key: Configuration key
            value: Configuration value
        """
        self._config[key] = value
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
    
    def save_token(self, token_data: Dict[str, Any]) -> None:
        """Save OneDrive authentication token.
        
        Args:
            token_data: Token data dictionary
        """
        with open(self.token_path, 'w') as f:
            json.dump(token_data, f)
        # Restrict permissions to owner only for security
        self.token_path.chmod(0o600)
    
    def load_token(self) -> Optional[Dict[str, Any]]:
        """Load OneDrive authentication token.
        
        Returns:
            Token data or None if not found
        """
        if self.token_path.exists():
            with open(self.token_path, 'r') as f:
                return json.load(f)
        return None
    
    def save_state(self, state_data: Dict[str, Any]) -> None:
        """Save sync state.
        
        Args:
            state_data: State data dictionary
        """
        with open(self.state_path, 'w') as f:
            json.dump(state_data, f, indent=2)
    
    def load_state(self) -> Dict[str, Any]:
        """Load sync state.
        
        Returns:
            State data dictionary
        """
        if self.state_path.exists():
            with open(self.state_path, 'r') as f:
                return json.load(f)
        return {'files': {}, 'last_sync': None}
