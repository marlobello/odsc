#!/usr/bin/env python3
"""OneDrive API client for ODSC."""

import logging
import os
import re
import secrets
import threading
import time
from email.utils import parsedate_to_datetime
from functools import wraps
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Tuple
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

import requests
import certifi
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception, before_sleep_log
from tenacity.wait import wait_base

from .error_handling import get_http_status, is_transient_error, log_exception
from .path_utils import SecurityError
from .quickxorhash import extract_quickxorhash, quickxorhash_file


logger = logging.getLogger(__name__)


class IntegrityVerificationError(requests.exceptions.ConnectionError):
    """Raised when transferred bytes do not match OneDrive's content hash."""


RETRY_AFTER_STATUS_CODES = {429, 503}
MAX_RETRY_AFTER_SECONDS = 300


def _parse_retry_after_header(
    value: Optional[str],
    now: Optional[datetime] = None,
    max_delay: int = MAX_RETRY_AFTER_SECONDS,
) -> float:
    """Return Retry-After delay seconds, clamped to a safe maximum."""
    if value is None:
        return 0.0

    value = value.strip()
    if not value:
        return 0.0

    try:
        delay = float(int(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return 0.0

        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        delay = (retry_at - now).total_seconds()

    delay = max(0.0, delay)
    return min(delay, float(max_delay))


def _get_retry_after_delay(exc: BaseException) -> float:
    """Extract a Retry-After delay from retryable throttling responses."""
    if get_http_status(exc) not in RETRY_AFTER_STATUS_CODES:
        return 0.0

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    retry_after = None
    for name, value in headers.items():
        if name.lower() == "retry-after":
            retry_after = value
            break
    return _parse_retry_after_header(retry_after)


class _RetryAfterWait(wait_base):
    """Tenacity wait strategy that honors Graph Retry-After throttling."""

    def __init__(self, fallback_wait: Callable[[Any], float]) -> None:
        self._fallback_wait = fallback_wait

    def __call__(self, retry_state: Any) -> float:
        fallback_delay = self._fallback_wait(retry_state)
        outcome = getattr(retry_state, "outcome", None)
        if outcome is None:
            return fallback_delay

        exc = outcome.exception()
        if exc is None:
            return fallback_delay

        retry_after_delay = _get_retry_after_delay(exc)
        return max(fallback_delay, retry_after_delay)


GRAPH_RETRY_WAIT = _RetryAfterWait(wait_random_exponential(multiplier=1, min=1, max=10))


class OneDriveClient:
    """Client for interacting with Microsoft OneDrive API."""
    
    # Microsoft Graph API endpoints
    # Using /consumers/ endpoint for personal Microsoft accounts (OneDrive Consumer)
    AUTH_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
    TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
    API_BASE = "https://graph.microsoft.com/v1.0"
    REDIRECT_URI = "http://localhost:8080"
    SCOPES = "files.readwrite offline_access User.Read"
    
    # Default public client ID for ODSC
    # Public client identifier for OneDrive Consumer (personal accounts)
    # Users can override this by providing their own client ID if needed
    DEFAULT_CLIENT_ID = "df3a0308-c302-4962-b115-08bd59526bc5"
    
    # Files larger than this are uploaded with a resumable upload session
    # instead of a single PUT. Microsoft Graph requires a session for files
    # above ~4 MiB and rejects large simple uploads.
    SIMPLE_UPLOAD_MAX_BYTES = 4 * 1024 * 1024
    # Upload-session fragment size. Graph requires a multiple of 320 KiB
    # (327680 bytes) for every fragment except the final one.
    UPLOAD_FRAGMENT_SIZE = 10 * 327680  # ~3.1 MiB

    def __init__(self, client_id: Optional[str] = None, token_data: Optional[Dict[str, Any]] = None):
        """Initialize OneDrive client.
        
        Args:
            client_id: Microsoft application client ID (optional, uses default if not provided)
            token_data: Existing token data (optional)
        """
        self.client_id = client_id or self.DEFAULT_CLIENT_ID
        self.token_data: Dict[str, Any] = {}
        self._session = requests.Session()
        self._session.verify = certifi.where()  # Explicit certificate validation
        self.state: Optional[str] = None  # For CSRF protection
        self._token_lock = threading.RLock()
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        with self._token_lock:
            self._set_token_data_locked(token_data or {})

    def _set_token_data_locked(self, token_data: Dict[str, Any]) -> None:
        """Replace cached token state while holding ``self._token_lock``."""
        self.token_data = dict(token_data)
        self._access_token = self.token_data.get('access_token')
        self._token_expires_at = float(self.token_data.get('expires_at', 0) or 0)
    
    def _sanitize_for_log(self, text: str) -> str:
        """Remove sensitive data from log output.
        
        Args:
            text: Text to sanitize
            
        Returns:
            Sanitized text with sensitive data redacted
        """
        # Redact tokens and codes
        text = re.sub(r'(access_token|refresh_token|code)["\']?\s*[:=]\s*["\']?[\w\-\.]+', 
                     r'\1=***REDACTED***', text, flags=re.IGNORECASE)
        text = re.sub(r'Bearer\s+[\w\-\.]+', 'Bearer ***REDACTED***', text, flags=re.IGNORECASE)
        return text

    def _log_request_exception(self, message: str, exc: BaseException, *, exc_info: bool = False) -> None:
        """Log request exceptions with sanitized output and appropriate severity."""
        log_exception(logger, message, exc, exc_info=exc_info, sanitizer=self._sanitize_for_log)

    def _log_http_error(self, method: str, endpoint: str, exc: requests.exceptions.HTTPError) -> None:
        """Log HTTP failures with transient/permanent severity."""
        status = get_http_status(exc)
        if status in (401, 403):
            logger.error(
                "Authentication/authorisation error %s for %s %s — token may be expired or revoked.",
                status,
                method,
                endpoint,
            )
            return
        if status == 404:
            logger.error("Requested OneDrive item was not found for %s %s", method, endpoint)
            return
        if status is not None and status >= 500:
            logger.warning("Transient server error %s for %s %s", status, method, endpoint)
            return
        logger.error("OneDrive request failed with HTTP %s for %s %s", status, method, endpoint)

    def _verify_download_integrity(
        self, metadata: Dict[str, Any], local_path: Path, remote_id: str
    ) -> None:
        """Fail the download when OneDrive's hash and the local bytes differ."""
        remote_hash = extract_quickxorhash(metadata)
        if not remote_hash:
            return

        try:
            local_hash = quickxorhash_file(local_path)
        except OSError as exc:
            logger.error(
                "Could not hash downloaded file %s for integrity verification; rejecting transfer",
                local_path,
                exc_info=True,
            )
            raise IntegrityVerificationError(
                f"Could not verify downloaded file {remote_id}: {exc}"
            ) from exc

        if local_hash != remote_hash:
            logger.error(
                "QuickXorHash mismatch after download for %s: local=%s remote=%s",
                remote_id,
                local_hash,
                remote_hash,
            )
            raise IntegrityVerificationError(
                f"QuickXorHash mismatch after downloading OneDrive item {remote_id}"
            )

    def _verify_upload_integrity(
        self, metadata: Dict[str, Any], local_path: Path, remote_path: str
    ) -> None:
        """Fail the upload when OneDrive reports a different content hash."""
        remote_hash = extract_quickxorhash(metadata)
        if not remote_hash:
            return

        try:
            local_hash = quickxorhash_file(local_path)
        except OSError:
            logger.warning(
                "Could not hash local upload %s for integrity verification; skipping check",
                local_path,
                exc_info=True,
            )
            return

        if local_hash != remote_hash:
            logger.error(
                "QuickXorHash mismatch after upload for %s: local=%s remote=%s",
                remote_path,
                local_hash,
                remote_hash,
            )
            raise IntegrityVerificationError(
                f"QuickXorHash mismatch after uploading {remote_path}"
            )
    
    def get_auth_url(self, state: Optional[str] = None) -> str:
        """Get OAuth2 authorization URL with CSRF protection.

        Args:
            state: Optional pre-generated state nonce for CSRF protection.
        
        Returns:
            Authorization URL
        """
        # Generate CSRF protection state parameter
        self.state = state or secrets.token_urlsafe(32)
        
        params = {
            'client_id': self.client_id,
            'scope': self.SCOPES,
            'response_type': 'code',
            'redirect_uri': self.REDIRECT_URI,
            'state': self.state,  # CSRF protection
        }
        auth_url = f"{self.AUTH_URL}?{urlencode(params)}"
        logger.info("Generated authorization URL with CSRF protection")
        return auth_url
    
    def validate_state(self, received_state: str) -> bool:
        """Validate OAuth state parameter for CSRF protection.
        
        Args:
            received_state: State parameter received from OAuth callback
            
        Returns:
            True if state is valid, False otherwise
        """
        if not self.state:
            logger.error("No state was generated - possible attack")
            return False
        
        is_valid = self.state == received_state
        if not is_valid:
            logger.error("State validation failed - possible CSRF attack")
        else:
            logger.info("State validation successful")
        
        # Clear state after validation (one-time use)
        self.state = None
        return is_valid
    
    def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token.
        
        Args:
            code: Authorization code
            
        Returns:
            Token data
        """
        data = {
            'client_id': self.client_id,
            'code': code,
            'redirect_uri': self.REDIRECT_URI,
            'grant_type': 'authorization_code',
        }
        
        logger.info("Exchanging authorization code for access token")
        
        try:
            response = requests.post(self.TOKEN_URL, data=data, verify=certifi.where(), timeout=30)
            logger.debug(f"Token exchange response status: {response.status_code}")
            response.raise_for_status()
            
            token_data = response.json()
            token_data['expires_at'] = time.time() + token_data.get('expires_in', 3600)
            with self._token_lock:
                self._set_token_data_locked(token_data)
            logger.info("Successfully obtained access token")
            return self.token_data
            
        except requests.exceptions.HTTPError as exc:
            self._log_http_error("POST", self.TOKEN_URL, exc)
            raise
        except requests.exceptions.RequestException as exc:
            self._log_request_exception("Token exchange failed", exc)
            raise
    
    def refresh_token(self) -> Dict[str, Any]:
        """Refresh access token using refresh token.
        
        Returns:
            New token data
        """
        with self._token_lock:
            refresh_token = self.token_data.get('refresh_token')
            if not refresh_token:
                logger.error("No refresh token available")
                raise ValueError("No refresh token available")
        
        logger.info("Refreshing access token")
        
        data = {
            'client_id': self.client_id,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }
        
        try:
            response = requests.post(self.TOKEN_URL, data=data, verify=certifi.where(), timeout=30)
            logger.debug(f"Refresh token response status: {response.status_code}")
            response.raise_for_status()
            
            token_data = response.json()
            token_data['expires_at'] = time.time() + token_data.get('expires_in', 3600)
            with self._token_lock:
                self._set_token_data_locked(token_data)
            logger.info("Successfully refreshed access token")
            return self.token_data
            
        except requests.exceptions.HTTPError as exc:
            self._log_http_error("POST", self.TOKEN_URL, exc)
            raise
        except requests.exceptions.RequestException as exc:
            self._log_request_exception("Token refresh failed", exc)
            raise
    
    def _ensure_token(self) -> None:
        """Ensure we have a valid access token, refreshing if necessary.
        
        Uses a lock to prevent multiple parallel workers from racing to
        refresh the same expired token simultaneously.
        """
        with self._token_lock:
            if not self.token_data:
                raise ValueError("Not authenticated. Call authenticate() first.")

            if self._token_expires_at < time.time() + 300:
                logger.info("Token expired or expiring soon, refreshing...")
                self.refresh_token()
    
    def _api_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make authenticated API request.
        
        Args:
            method: HTTP method
            endpoint: API endpoint (without base URL)
            **kwargs: Additional request arguments
            
        Returns:
            Response object
        """
        self._ensure_token()
        
        headers = kwargs.pop('headers', {})
        if not self._access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")
        headers['Authorization'] = f"Bearer {self._access_token}"
        
        # Apply a default timeout so stalled transfers never block a worker
        # indefinitely. Callers may override by passing timeout= explicitly.
        kwargs.setdefault('timeout', (10, 300))  # (connect, read) seconds

        url = f"{self.API_BASE}{endpoint}"
        try:
            response = self._session.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as exc:
            self._log_http_error(method, endpoint, exc)
            raise
        except requests.exceptions.RequestException as exc:
            self._log_request_exception(f"Request failed for {method} {endpoint}", exc)
            raise
    
    def get_user_info(self) -> Dict[str, Any]:
        """Get user profile information.
        
        Returns:
            User profile data including displayName, mail, userPrincipalName, etc.
        """
        response = self._api_request('GET', '/me')
        return response.json()
    
    def list_files(self, path: str = "/", paginate: bool = True) -> List[Dict[str, Any]]:
        """List files in OneDrive directory with pagination support.
        
        Args:
            path: Directory path (default: root)
            paginate: Whether to follow pagination links (default: True)
            
        Returns:
            List of file/folder metadata
        """
        if path == "/":
            endpoint = "/me/drive/root/children"
        else:
            # Remove leading/trailing slashes
            path = path.strip("/")
            endpoint = f"/me/drive/root:/{path}:/children"
        
        all_items = []
        url = None  # Will be set to full URL for pagination
        
        while True:
            if url:
                # Use full URL for paginated requests
                response = self._api_request_url(url)
            else:
                # Use endpoint for first request
                response = self._api_request('GET', endpoint)
            
            data = response.json()
            items = data.get('value', [])
            all_items.extend(items)
            
            # Check for pagination
            next_link = data.get('@odata.nextLink')
            if not paginate or not next_link:
                break
            
            url = next_link
            logger.debug(f"Following pagination link, fetched {len(all_items)} items so far")
        
        logger.info(f"Listed {len(all_items)} items from {path}")
        return all_items
    
    def _api_request_url(self, url: str, **kwargs) -> requests.Response:
        """Make authenticated API request to a full URL (for pagination).
        
        Args:
            url: Full URL including domain
            **kwargs: Additional request arguments
            
        Returns:
            Response object
            
        Raises:
            SecurityError: If URL is not from trusted Microsoft domain
        """
        # Validate URL is from Microsoft Graph API (SSRF protection)
        parsed = urlparse(url)
        if not (parsed.scheme == 'https' and 
                parsed.hostname == 'graph.microsoft.com' and
                parsed.path.startswith('/v1.0/')):
            raise SecurityError(
                f"Untrusted pagination URL: {url} "
                f"(scheme={parsed.scheme}, host={parsed.hostname}, path={parsed.path})"
            )
        
        self._ensure_token()
        
        headers = kwargs.pop('headers', {})
        if not self._access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")
        headers['Authorization'] = f"Bearer {self._access_token}"
        
        kwargs.setdefault('timeout', (10, 300))
        try:
            response = self._session.request('GET', url, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as exc:
            self._log_http_error('GET', parsed.path, exc)
            raise
        except requests.exceptions.RequestException as exc:
            self._log_request_exception(f"Request failed for GET {parsed.path}", exc)
            raise
    
    def get_delta(self, delta_token: Optional[str] = None) -> Tuple[List[Dict[str, Any]], str]:
        """Get changes since last sync using delta query.
        
        This is much more efficient than list_all_files() for incremental syncs.
        The delta query returns only items that have changed since the last query.
        
        Args:
            delta_token: Token from previous delta query (None for initial sync)
            
        Returns:
            Tuple of (list of changed items, new delta token)
            
        Note:
            - Initial call (delta_token=None) returns all items
            - Subsequent calls return only changes since last delta_token
            - Store and reuse the returned delta_token for incremental syncs
        """
        if delta_token:
            # Resume from saved position using full deltaLink URL
            url = delta_token
            logger.info("Fetching incremental changes using delta query")
        else:
            # Start new delta query from root
            url = f"{self.API_BASE}/me/drive/root/delta"
            logger.info("Starting initial delta query (will fetch all items)")
        
        all_changes = []
        
        while True:
            if url.startswith('http'):
                # Full URL (pagination or deltaLink)
                response = self._api_request_url(url)
            else:
                # Endpoint path
                response = self._api_request('GET', url)
            
            data = response.json()
            items = data.get('value', [])
            all_changes.extend(items)
            
            # Check for next page
            next_link = data.get('@odata.nextLink')
            if next_link:
                url = next_link
                logger.debug(f"Following delta pagination, {len(all_changes)} changes so far")
                continue
            
            # Check for delta link (save this for next query)
            delta_link = data.get('@odata.deltaLink')
            if delta_link:
                logger.info(f"Delta query complete: {len(all_changes)} items")
                return all_changes, delta_link

            # A well-formed delta query always terminates with either a
            # nextLink or a deltaLink. Reaching here means the response was
            # malformed/truncated. Raise rather than returning a None token,
            # which a caller could persist and corrupt the next sync.
            raise RuntimeError(
                "Delta query ended without a deltaLink or nextLink; "
                "the response was malformed or truncated."
            )
    
    def get_file_by_path(self, remote_path: str) -> Optional[Dict[str, Any]]:
        """Get file metadata by path.
        
        Args:
            remote_path: Remote file path
            
        Returns:
            File metadata or None if not found
        """
        remote_path = remote_path.lstrip('/')
        
        try:
            endpoint = f"/me/drive/root:/{remote_path}"
            response = self._api_request('GET', endpoint)
            return response.json()
        except requests.exceptions.HTTPError as exc:
            if get_http_status(exc) == 404:
                logger.error("File not found on OneDrive: %s", remote_path)
                return None
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=GRAPH_RETRY_WAIT,
        retry=retry_if_exception(is_transient_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    def download_file(self, file_id: str, local_path: Path, chunk_size: int = 65536) -> Dict[str, Any]:
        """Download file from OneDrive with retry logic.
        
        Args:
            file_id: OneDrive file ID
            local_path: Local destination path
            chunk_size: Bytes per read when streaming the response body.
                Larger values reduce per-chunk overhead; defaults to 64 KB.
            
        Returns:
            File metadata including eTag
            
        Raises:
            Exception: If download fails after all retries
        """
        # Get metadata first
        metadata = self.get_file_metadata(file_id)
        
        endpoint = f"/me/drive/items/{file_id}/content"
        response = self._api_request('GET', endpoint, stream=True)
        
        # Create parent directories before opening the temp file. The temp file
        # must live in the same directory as the final destination so the final
        # replace is atomic on the target filesystem.
        local_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = local_path.parent / f"{local_path.name}.{secrets.token_hex(8)}.odsc_tmp"
        try:
            with response:
                # Use os.open with O_CREAT|O_EXCL to prevent temp-file collisions
                # and to honour the user's umask for the final file permissions.
                fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
                with os.fdopen(fd, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        f.write(chunk)

                    f.flush()
                    os.fsync(f.fileno())

            self._verify_download_integrity(metadata, temp_path, file_id)
            os.replace(temp_path, local_path)

            logger.info(f"Downloaded: {local_path}")
            return metadata
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(f"Failed to clean up temp download file: {temp_path}", exc_info=True)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=GRAPH_RETRY_WAIT,
        retry=retry_if_exception(is_transient_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    def upload_file(self, local_path: Path, remote_path: str) -> Dict[str, Any]:
        """Upload file to OneDrive with retry logic.

        Small files are sent with a single PUT. Files larger than
        :attr:`SIMPLE_UPLOAD_MAX_BYTES` use a resumable upload session, which
        Microsoft Graph requires for large files (a simple PUT fails for
        files above ~250 MB and is discouraged above ~4 MiB).

        Args:
            local_path: Local file path
            remote_path: Remote destination path

        Returns:
            Upload response metadata including eTag

        Raises:
            Exception: If upload fails after all retries
        """
        # Remove leading slash
        remote_path = remote_path.lstrip('/')

        file_size = local_path.stat().st_size
        if file_size > self.SIMPLE_UPLOAD_MAX_BYTES:
            return self._upload_large_file(local_path, remote_path, file_size)

        endpoint = f"/me/drive/root:/{remote_path}:/content"

        with open(local_path, 'rb') as f:
            headers = {'Content-Type': 'application/octet-stream'}
            response = self._api_request('PUT', endpoint, data=f, headers=headers)

        metadata = response.json()
        self._verify_upload_integrity(metadata, local_path, remote_path)
        logger.info(f"Uploaded: {local_path} -> {remote_path}")
        return metadata

    def _create_upload_session(self, remote_path: str) -> str:
        """Create a resumable upload session and return its upload URL."""
        endpoint = f"/me/drive/root:/{remote_path}:/createUploadSession"
        body = {"item": {"@microsoft.graph.conflictBehavior": "replace"}}
        response = self._api_request('POST', endpoint, json=body)
        upload_url = response.json().get('uploadUrl')
        if not upload_url:
            raise RuntimeError("OneDrive did not return an upload URL for the session")
        return upload_url

    def _upload_large_file(
        self, local_path: Path, remote_path: str, file_size: int
    ) -> Dict[str, Any]:
        """Upload a large file using a resumable upload session.

        Uploads the file in fixed-size fragments. The pre-authenticated
        ``uploadUrl`` returned by Graph is used directly (it must not carry the
        bearer token). If any fragment fails the session is cancelled so a
        partial upload is not left behind, and the error propagates to the
        ``@retry`` wrapper which restarts with a fresh session.

        Args:
            local_path: Local file path.
            remote_path: Remote destination path (no leading slash).
            file_size: Size of the file in bytes.

        Returns:
            Final item metadata returned by OneDrive.
        """
        upload_url = self._create_upload_session(remote_path)
        fragment_size = self.UPLOAD_FRAGMENT_SIZE
        logger.info(
            f"Uploading large file via session: {local_path} -> {remote_path} "
            f"({file_size} bytes, {fragment_size}-byte fragments)"
        )

        final_metadata: Optional[Dict[str, Any]] = None
        try:
            with open(local_path, 'rb') as f:
                start = 0
                while start < file_size:
                    chunk = f.read(fragment_size)
                    if not chunk:
                        break
                    end = start + len(chunk) - 1
                    headers = {
                        'Content-Length': str(len(chunk)),
                        'Content-Range': f"bytes {start}-{end}/{file_size}",
                    }
                    response = self._session.put(
                        upload_url, data=chunk, headers=headers, timeout=(10, 300)
                    )
                    response.raise_for_status()
                    # 202 => more fragments expected; 200/201 => upload complete.
                    if response.status_code in (200, 201):
                        final_metadata = response.json()
                    start = end + 1

            if final_metadata is None:
                raise RuntimeError(
                    f"Upload session for {remote_path} completed without final metadata"
                )
        except Exception as exc:
            # Best-effort cancellation so OneDrive does not retain a partial session.
            try:
                self._session.delete(upload_url, timeout=(10, 60))
            except requests.exceptions.RequestException:
                logger.debug("Could not cancel upload session", exc_info=True)
            # The pre-authenticated uploadUrl embeds a temporary credential; never
            # let it reach logs (tenacity/daemon log exception text on retry).
            if isinstance(exc, requests.exceptions.RequestException):
                raise self._redact_upload_url_error(exc, upload_url) from None
            raise

        self._verify_upload_integrity(final_metadata, local_path, remote_path)
        logger.info(f"Uploaded (session): {local_path} -> {remote_path}")
        return final_metadata

    def _redact_upload_url_error(
        self, exc: requests.exceptions.RequestException, upload_url: str
    ) -> requests.exceptions.RequestException:
        """Return a copy of *exc* with the pre-authenticated upload URL redacted.

        Preserves the exception class (so retry classification via
        ``is_transient_error`` still works) and any attached HTTP response.
        """
        message = str(exc).replace(upload_url, "<redacted-upload-url>")
        if isinstance(exc, requests.exceptions.HTTPError):
            return requests.exceptions.HTTPError(
                message, response=getattr(exc, "response", None)
            )
        try:
            return type(exc)(message)
        except Exception:
            return requests.exceptions.RequestException(message)
    
    def create_folder(self, folder_path: str) -> Dict[str, Any]:
        """Create a folder on OneDrive.
        
        Args:
            folder_path: Relative path of folder to create (e.g., "Documents/NewFolder")
            
        Returns:
            Folder metadata including ID
        """
        folder_path = folder_path.lstrip('/')
        
        # Split into parent and folder name
        path_parts = Path(folder_path).parts
        if len(path_parts) == 1:
            # Root level folder
            parent_endpoint = "/me/drive/root/children"
            folder_name = path_parts[0]
        else:
            # Nested folder
            parent_path = str(Path(*path_parts[:-1]))
            parent_endpoint = f"/me/drive/root:/{parent_path}:/children"
            folder_name = path_parts[-1]
        
        # Create folder
        data = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail"
        }
        
        try:
            response = self._api_request('POST', parent_endpoint, json=data)
            metadata = response.json()
            logger.info(f"Created folder: {folder_path}")
            return metadata
        except requests.exceptions.HTTPError as exc:
            status = get_http_status(exc)
            response_text = exc.response.text.lower() if exc.response is not None else ""
            if status == 409 or "already exists" in response_text or "name already exists" in response_text:
                logger.info(f"Folder already exists: {folder_path}")
                try:
                    endpoint = f"/me/drive/root:/{folder_path}"
                    response = self._api_request('GET', endpoint)
                    return response.json()
                except requests.exceptions.RequestException as get_err:
                    self._log_request_exception(
                        f"Could not fetch existing folder metadata for {folder_path}",
                        get_err,
                    )
            raise
    
    def move_item(
        self,
        item_id: str,
        new_name: str,
        new_parent_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Rename and/or move an item on OneDrive in a single PATCH request.

        Args:
            item_id: OneDrive item ID of the file or folder to move.
            new_name: New name for the item (basename only).
            new_parent_path: New parent folder path relative to the sync root
                (e.g. ``"Photos/Vacation"``).  Pass ``""`` to move to the root.
                If *None* the parent is unchanged (rename only).

        Returns:
            Updated item metadata from OneDrive.
        """
        body: Dict[str, Any] = {"name": new_name}
        if new_parent_path is not None:
            root_ref = "/drive/root:"
            if new_parent_path:
                root_ref = f"/drive/root:/{new_parent_path}"
            body["parentReference"] = {"path": root_ref}

        response = self._api_request("PATCH", f"/me/drive/items/{item_id}", json=body)
        metadata = response.json()
        logger.info(f"Moved/renamed OneDrive item {item_id} → {new_name}")
        return metadata

    def delete_file(self, file_id: str) -> None:
        """Delete file from OneDrive.
        
        Args:
            file_id: OneDrive file ID
        """
        endpoint = f"/me/drive/items/{file_id}"
        self._api_request('DELETE', endpoint)
        logger.info(f"Deleted file: {file_id}")
    
    def get_file_metadata(self, file_id: str) -> Dict[str, Any]:
        """Get file metadata.
        
        Args:
            file_id: OneDrive file ID
            
        Returns:
            File metadata
        """
        endpoint = f"/me/drive/items/{file_id}"
        response = self._api_request('GET', endpoint)
        return response.json()
