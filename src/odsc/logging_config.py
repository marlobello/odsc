#!/usr/bin/env python3
"""Logging configuration for ODSC."""

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logging(level: Optional[str] = None, log_file: Optional[Path] = None) -> None:
    """Setup logging configuration for ODSC.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
               If None, reads from ODSC_LOG_LEVEL env var, defaults to INFO
        log_file: Optional path to log file. If None, logs to console only
    """
    # Determine log level
    if level is None:
        import os
        level = os.environ.get('ODSC_LOG_LEVEL', 'INFO').upper()
    
    numeric_level = getattr(logging, level, logging.INFO)
    
    # Create formatter
    detailed_formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    simple_formatter = logging.Formatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Setup root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    
    # Use detailed formatter for DEBUG, simple for others
    if numeric_level == logging.DEBUG:
        console_handler.setFormatter(detailed_formatter)
    else:
        console_handler.setFormatter(simple_formatter)
    
    root_logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)  # Always log everything to file
        file_handler.setFormatter(detailed_formatter)
        root_logger.addHandler(file_handler)
    
    # Suppress noisy third-party loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    
    # Log startup
    logger = logging.getLogger(__name__)
    logger.info(f"ODSC logging initialized at {level} level")
    if log_file:
        logger.info(f"Logging to file: {log_file}")


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)
