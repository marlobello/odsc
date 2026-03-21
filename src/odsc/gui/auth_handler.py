"""OAuth authentication handler for ODSC GUI.

Re-exports :class:`~odsc.oauth_callback.AuthCallbackHandler` from the shared
module so existing imports in GUI code continue to work unchanged.
"""

from odsc.oauth_callback import AuthCallbackHandler

__all__ = ["AuthCallbackHandler"]
