"""Configuration validators for ODSC."""

import logging
from pathlib import Path
from typing import Any
import uuid

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


class ConfigValidator:
    """Base class for configuration validators."""
    
    def validate(self, value: Any) -> Any:
        """Validate and normalize a configuration value.
        
        Args:
            value: Raw configuration value
            
        Returns:
            Validated and normalized value
            
        Raises:
            ValidationError: If validation fails
        """
        raise NotImplementedError


class SyncIntervalValidator(ConfigValidator):
    """Validates sync interval (seconds between syncs)."""
    
    MIN_INTERVAL = 60  # 1 minute
    MAX_INTERVAL = 86400  # 24 hours
    
    def validate(self, value: Any) -> int:
        try:
            interval = int(value)
        except (ValueError, TypeError):
            raise ValidationError(f"Sync interval must be an integer, got: {value}")
        
        if interval < self.MIN_INTERVAL:
            raise ValidationError(
                f"Sync interval must be at least {self.MIN_INTERVAL} seconds"
            )
        
        if interval > self.MAX_INTERVAL:
            raise ValidationError(
                f"Sync interval must be at most {self.MAX_INTERVAL} seconds (24 hours)"
            )
        
        return interval


class SyncDirectoryValidator(ConfigValidator):
    """Validates sync directory path."""
    
    def validate(self, value: Any) -> str:
        if not isinstance(value, (str, Path)):
            raise ValidationError(f"Sync directory must be a string or Path, got: {type(value)}")
        
        path = Path(value).expanduser().resolve()
        
        # Check parent exists
        if not path.parent.exists():
            raise ValidationError(
                f"Parent directory does not exist: {path.parent}"
            )
        
        # Create sync directory if it doesn't exist
        if not path.exists():
            try:
                path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created sync directory: {path}")
            except Exception as e:
                raise ValidationError(
                    f"Failed to create sync directory {path}: {e}"
                )
        
        # Verify it's a directory
        if not path.is_dir():
            raise ValidationError(
                f"Sync directory path exists but is not a directory: {path}"
            )
        
        # Check write permissions
        if not path.exists():
            # Directory doesn't exist, check parent
            test_path = path.parent
        else:
            test_path = path
        
        if not test_path.exists() or not test_path.is_dir():
            raise ValidationError(
                f"Sync directory is not accessible: {path}"
            )
        
        return str(path)


class LogLevelValidator(ConfigValidator):
    """Validates log level."""
    
    VALID_LEVELS = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
    
    def validate(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"Log level must be a string, got: {type(value)}")
        
        level = value.upper()
        
        if level not in self.VALID_LEVELS:
            raise ValidationError(
                f"Invalid log level: {value}. Must be one of: {', '.join(sorted(self.VALID_LEVELS))}"
            )
        
        return level


class ClientIdValidator(ConfigValidator):
    """Validates OneDrive client ID (must be valid UUID format)."""
    
    def validate(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"Client ID must be a string, got: {type(value)}")
        
        # Remove whitespace
        client_id = value.strip()
        
        if not client_id:
            raise ValidationError("Client ID cannot be empty")
        
        # Verify it's a valid UUID format
        try:
            uuid.UUID(client_id)
        except ValueError:
            raise ValidationError(
                f"Client ID must be a valid UUID format, got: {client_id}"
            )
        
        return client_id


class BooleanValidator(ConfigValidator):
    """Validates boolean values."""
    
    TRUE_VALUES = {'true', '1', 'yes', 'on', 'enabled'}
    FALSE_VALUES = {'false', '0', 'no', 'off', 'disabled'}
    
    def validate(self, value: Any) -> bool:
        # Already a boolean
        if isinstance(value, bool):
            return value
        
        # Convert string to boolean
        if isinstance(value, str):
            normalized = value.lower().strip()
            
            if normalized in self.TRUE_VALUES:
                return True
            
            if normalized in self.FALSE_VALUES:
                return False
            
            raise ValidationError(
                f"Invalid boolean value: {value}. Expected: true/false, yes/no, 1/0, on/off, enabled/disabled"
            )
        
        # Try to convert to bool directly
        try:
            return bool(value)
        except:
            raise ValidationError(f"Cannot convert to boolean: {value}")


class IntegerValidator(ConfigValidator):
    """Validates integer values with optional min/max bounds."""
    
    def __init__(self, min_value: int = None, max_value: int = None):
        self.min_value = min_value
        self.max_value = max_value
    
    def validate(self, value: Any) -> int:
        try:
            int_value = int(value)
        except (ValueError, TypeError):
            raise ValidationError(f"Must be an integer, got: {value}")
        
        if self.min_value is not None and int_value < self.min_value:
            raise ValidationError(
                f"Must be at least {self.min_value}, got: {int_value}"
            )
        
        if self.max_value is not None and int_value > self.max_value:
            raise ValidationError(
                f"Must be at most {self.max_value}, got: {int_value}"
            )
        
        return int_value


class StringValidator(ConfigValidator):
    """Validates string values with optional constraints."""
    
    def __init__(self, min_length: int = 0, max_length: int = None, allow_empty: bool = True):
        self.min_length = min_length
        self.max_length = max_length
        self.allow_empty = allow_empty
    
    def validate(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"Must be a string, got: {type(value)}")
        
        if not self.allow_empty and not value.strip():
            raise ValidationError("Cannot be empty")
        
        if len(value) < self.min_length:
            raise ValidationError(
                f"Must be at least {self.min_length} characters, got: {len(value)}"
            )
        
        if self.max_length is not None and len(value) > self.max_length:
            raise ValidationError(
                f"Must be at most {self.max_length} characters, got: {len(value)}"
            )
        
        return value


# Registry of validators for known config keys
VALIDATORS = {
    'sync_interval': SyncIntervalValidator(),
    'sync_directory': SyncDirectoryValidator(),
    'log_level': LogLevelValidator(),
    'client_id': ClientIdValidator(),
    'auto_start': BooleanValidator(),
}


def validate_config_value(key: str, value: Any) -> Any:
    """Validate a configuration value using registered validators.
    
    Args:
        key: Configuration key
        value: Value to validate
        
    Returns:
        Validated and normalized value
        
    Raises:
        ValidationError: If validation fails
    """
    if key in VALIDATORS:
        return VALIDATORS[key].validate(value)
    
    # Unknown keys pass through unchanged
    return value
