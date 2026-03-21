"""TokenStore — encrypted OAuth token persistence.

Extracted from :class:`~odsc.config.Config` so token encryption/decryption
has a single, testable home. :class:`~odsc.config.Config` composes this class
and delegates ``save_token`` / ``load_token`` to it.
"""

import base64
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from cryptography.fernet import Fernet, InvalidToken
import keyring

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "odsc"
_KEYRING_KEY_NAME = "token_encryption_key"


class TokenStore:
    """Encrypts, stores and retrieves the OneDrive OAuth token.

    The encryption key is kept in the system keyring (via the ``keyring``
    library). The encrypted token is written as a binary file at
    ``token_path``.

    Args:
        token_path: Path where the encrypted token file is stored.
    """

    def __init__(self, token_path: Path) -> None:
        self.token_path = token_path

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def save(self, token_data: Dict[str, Any]) -> None:
        """Encrypt *token_data* and write it to :attr:`token_path`.

        Args:
            token_data: Raw token dict (access_token, refresh_token, …).
        """
        encrypted = self._encrypt(token_data)
        self.token_path.write_bytes(encrypted)
        self.token_path.chmod(0o600)
        logger.info("Token saved with encryption")

    def load(self) -> Optional[Dict[str, Any]]:
        """Read and decrypt the stored token.

        Returns:
            Token dict, or ``None`` if no token file exists or decryption
            fails.
        """
        if not self.token_path.exists():
            return None

        try:
            encrypted_data = self.token_path.read_bytes()
            token_data = self._decrypt(encrypted_data)
            logger.info("Token loaded and decrypted successfully")
            return token_data

        except ValueError as exc:
            # Corrupted or wrong-key token — safe to delete and re-auth
            logger.warning(f"Could not decrypt token: {exc}")
            logger.warning("Token file may be from old version — please re-authenticate")
            self.token_path.unlink(missing_ok=True)
            return None

        except Exception as exc:
            logger.error(f"Error loading token: {exc}")
            return None

    def delete(self) -> None:
        """Delete the token file (e.g. on logout)."""
        self.token_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _get_key(self) -> bytes:
        """Return the Fernet encryption key, creating it if necessary."""
        key_str = keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME)

        if key_str:
            return base64.b64decode(key_str.encode())

        # If a token file already exists the key *must* be in the keyring.
        # Generating a new key would corrupt the existing token file.
        if self.token_path.exists():
            raise RuntimeError(
                "Encryption key not found in keyring but token file exists. "
                "The keyring may be locked or temporarily unavailable."
            )

        key = Fernet.generate_key()
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME, base64.b64encode(key).decode())
        logger.info("Generated new token encryption key")
        return key

    def _encrypt(self, token_data: Dict[str, Any]) -> bytes:
        fernet = Fernet(self._get_key())
        return fernet.encrypt(json.dumps(token_data).encode())

    def _decrypt(self, encrypted_data: bytes) -> Dict[str, Any]:
        try:
            fernet = Fernet(self._get_key())
            return json.loads(fernet.decrypt(encrypted_data).decode())
        except InvalidToken:
            raise ValueError("Invalid or corrupted token data")
        except ValueError:
            raise
        except Exception:
            # Keyring or other transient error — re-raise so caller skips deletion
            raise
