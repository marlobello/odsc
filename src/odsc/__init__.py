"""OneDrive Sync Client (ODSC) - A Linux sync client for Microsoft OneDrive."""

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    try:
        __version__ = _pkg_version("odsc")
    except PackageNotFoundError:
        # Running from source without installing
        from pathlib import Path
        _ver_file = Path(__file__).parent.parent.parent / "VERSION"
        __version__ = _ver_file.read_text().strip() if _ver_file.exists() else "unknown"
except ImportError:
    # Python < 3.8 fallback (shouldn't happen given our requirements)
    __version__ = "unknown"

__author__ = 'Marlo Bell'
__license__ = 'MIT'

from .config import Config
from .onedrive_client import OneDriveClient

__all__ = ['Config', 'OneDriveClient']
