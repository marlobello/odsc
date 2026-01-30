#!/usr/bin/env python3
"""GNOME GTK GUI for ODSC - Compatibility wrapper.

This module provides backward compatibility by importing from the new modular structure.
The actual implementation has been split into multiple modules under src/odsc/gui/.
"""

# Import everything from the new modular structure
from .gui import (
    OneDriveGUI,
    DialogHelper,
    AuthInfoDialog,
    SettingsDialog,
    AuthCallbackHandler,
    main,
)

__all__ = [
    'OneDriveGUI',
    'DialogHelper',
    'AuthInfoDialog',
    'SettingsDialog',
    'AuthCallbackHandler',
    'main',
]


if __name__ == '__main__':
    main()
