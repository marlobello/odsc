"""OneDrive Sync Client (ODSC) - A Linux sync client for Microsoft OneDrive."""

__version__ = '0.1.0'
__author__ = 'Marlo Bell'
__license__ = 'MIT'

from .config import Config
from .onedrive_client import OneDriveClient

__all__ = ['Config', 'OneDriveClient']
