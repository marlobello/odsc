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


class SyncIntervalValidator(IntegerValidator):
    """Validates sync interval (seconds between syncs).
    
    .. deprecated::
        Use ``IntegerValidator(min_value=60, max_value=86400)`` directly.
        This class exists only for backward compatibility.
    """

    MIN_INTERVAL = 60    # 1 minute
    MAX_INTERVAL = 86400  # 24 hours

    def __init__(self) -> None:
        super().__init__(min_value=self.MIN_INTERVAL, max_value=self.MAX_INTERVAL)


class SyncDirectoryValidator(ConfigValidator):
    """Validates sync directory path.

    .. warning::
        This validator has a side-effect: if the given path does not exist it
        will attempt to **create** the directory (and any missing parents).
        Call :func:`prepare_sync_directory` explicitly when you want the
        creation behaviour; when you only want to validate an existing path
        use a standard path check instead.
    """
    
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
        import os
        if not os.access(path, os.W_OK):
            raise ValidationError(
                f"Sync directory is not writable: {path}"
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
        except (TypeError, ValueError):
            raise ValidationError(f"Cannot convert to boolean: {value}")


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


class MaxSyncWorkersValidator(IntegerValidator):
    """Validates the number of parallel file transfer workers.
    
    .. deprecated::
        Use ``IntegerValidator(min_value=1, max_value=16)`` directly.
        This class exists only for backward compatibility.
    """

    def __init__(self) -> None:
        super().__init__(min_value=1, max_value=16)


class DownloadChunkSizeValidator(IntegerValidator):
    """Validates the chunk size used when streaming downloads (bytes).

    Larger values reduce Python overhead per chunk and generally improve
    throughput on fast connections.  Smaller values reduce memory use.

    .. deprecated::
        Use ``IntegerValidator(min_value=4096, max_value=16_777_216)`` directly.
        This class exists only for backward compatibility.
    """

    def __init__(self) -> None:
        super().__init__(min_value=4096, max_value=16_777_216)


# Registry of validators for known config keys
VALIDATORS = {
    'sync_interval': SyncIntervalValidator(),
    'sync_directory': SyncDirectoryValidator(),
    'log_level': LogLevelValidator(),
    'client_id': ClientIdValidator(),
    'auto_start': BooleanValidator(),
    'max_sync_workers': MaxSyncWorkersValidator(),
    'download_chunk_size': DownloadChunkSizeValidator(),
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
    
    # Warn about unrecognised keys — likely a typo
    logger.warning(f"Unknown config key '{key}' — value accepted but no validation applied")
    return value
