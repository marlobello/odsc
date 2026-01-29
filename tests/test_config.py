#!/usr/bin/env python3
"""Basic tests for ODSC configuration."""

import tempfile
import json
from pathlib import Path

from odsc.config import Config


def test_config_initialization():
    """Test configuration initialization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        config = Config(config_dir)
        
        # Check config file was created
        assert config.config_path.exists()
        
        # Check default values
        assert config.sync_interval == 300
        assert config.client_id == ''


def test_config_save_load():
    """Test configuration save and load."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Create and save config
        config1 = Config(config_dir)
        # Use a valid UUID-like client ID for testing
        config1.set('client_id', 'df3a0308-c302-4962-b115-08bd59526bc5')
        config1.set('sync_interval', 600)
        
        # Load config in new instance
        config2 = Config(config_dir)
        assert config2.get('client_id') == 'df3a0308-c302-4962-b115-08bd59526bc5'
        assert config2.sync_interval == 600


def test_token_save_load():
    """Test token save and load."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        config = Config(config_dir)
        
        # Save token
        token_data = {
            'access_token': 'test-access-token',
            'refresh_token': 'test-refresh-token',
            'expires_in': 3600
        }
        config.save_token(token_data)
        
        # Load token
        loaded_token = config.load_token()
        assert loaded_token == token_data
        assert loaded_token['access_token'] == 'test-access-token'


def test_state_save_load():
    """Test sync state save and load."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        config = Config(config_dir)
        
        # Save state
        state_data = {
            'files': {
                'test.txt': {'mtime': 1234567890, 'synced': True}
            },
            'last_sync': '2024-01-01T00:00:00'
        }
        config.save_state(state_data)
        
        # Load state
        loaded_state = config.load_state()
        assert loaded_state == state_data
        assert 'test.txt' in loaded_state['files']


if __name__ == '__main__':
    print("Running configuration tests...")
    
    test_config_initialization()
    print("✓ Configuration initialization")
    
    test_config_save_load()
    print("✓ Configuration save/load")
    
    test_token_save_load()
    print("✓ Token save/load")
    
    test_state_save_load()
    print("✓ State save/load")
    
    print("\nAll tests passed!")
