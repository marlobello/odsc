#!/usr/bin/env python3
"""OneDrive API client for ODSC."""

import logging
import os
import time
import re
import secrets
from functools import wraps
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

import requests
import certifi

from .path_utils import SecurityError


logger = logging.getLogger(__name__)


def retry_on_failure(max_retries: int = 3):
    """Decorator to retry a function on failure with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                        logger.warning(f"{func.__name__} attempt {attempt + 1} failed, retrying in {wait_time}s: {e}")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"{func.__name__} failed after {max_retries} attempts: {e}")
            raise Exception(f"{func.__name__} failed after {max_retries} attempts: {last_error}")
        return wrapper
    return decorator


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
    
    def __init__(self, client_id: Optional[str] = None, token_data: Optional[Dict[str, Any]] = None):
        """Initialize OneDrive client.
        
        Args:
            client_id: Microsoft application client ID (optional, uses default if not provided)
            token_data: Existing token data (optional)
        """
        self.client_id = client_id or self.DEFAULT_CLIENT_ID
        self.token_data = token_data or {}
        self._session = requests.Session()
        self._session.verify = certifi.where()  # Explicit certificate validation
        self.state: Optional[str] = None  # For CSRF protection
    
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
    
    def get_auth_url(self) -> str:
        """Get OAuth2 authorization URL with CSRF protection.
        
        Returns:
            Authorization URL
        """
        # Generate CSRF protection state parameter
        self.state = secrets.token_urlsafe(32)
        
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
            
            if response.status_code != 200:
                logger.error(f"Token exchange failed with status {response.status_code}")
                logger.error(f"Response: {self._sanitize_for_log(response.text)}")
            
            response.raise_for_status()
            
            self.token_data = response.json()
            self.token_data['expires_at'] = time.time() + self.token_data.get('expires_in', 3600)
            logger.info("Successfully obtained access token")
            return self.token_data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Token exchange request failed: {self._sanitize_for_log(str(e))}")
            raise
    
    def refresh_token(self) -> Dict[str, Any]:
        """Refresh access token using refresh token.
        
        Returns:
            New token data
        """
        if 'refresh_token' not in self.token_data:
            logger.error("No refresh token available")
            raise ValueError("No refresh token available")
        
        logger.info("Refreshing access token")
        
        data = {
            'client_id': self.client_id,
            'refresh_token': self.token_data['refresh_token'],
            'grant_type': 'refresh_token',
        }
        
        try:
            response = requests.post(self.TOKEN_URL, data=data, verify=certifi.where(), timeout=30)
            logger.debug(f"Refresh token response status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"Token refresh failed with status {response.status_code}")
                logger.error(f"Response: {self._sanitize_for_log(response.text)}")
            
            response.raise_for_status()
            
            self.token_data = response.json()
            self.token_data['expires_at'] = time.time() + self.token_data.get('expires_in', 3600)
            logger.info("Successfully refreshed access token")
            return self.token_data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Token refresh failed: {self._sanitize_for_log(str(e))}")
            raise
    
    def _ensure_token(self) -> None:
        """Ensure we have a valid access token."""
        if not self.token_data:
            raise ValueError("Not authenticated. Call authenticate() first.")
        
        # Refresh token if expired or about to expire (5 min buffer)
        if self.token_data.get('expires_at', 0) < time.time() + 300:
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
        headers['Authorization'] = f"Bearer {self.token_data['access_token']}"
        
        url = f"{self.API_BASE}{endpoint}"
        response = self._session.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response
    
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
        headers['Authorization'] = f"Bearer {self.token_data['access_token']}"
        
        response = self._session.request('GET', url, headers=headers, **kwargs)
        response.raise_for_status()
        return response
    
    def get_delta(self, delta_token: Optional[str] = None) -> tuple[List[Dict[str, Any]], str]:
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
            
            # Should not reach here, but handle gracefully
            logger.warning("Delta query ended without deltaLink")
            break
        
        return all_changes, None
    
    def list_all_files(self, path: str = "/") -> List[Dict[str, Any]]:
        """Recursively list all files and folders in OneDrive.
        
        Args:
            path: Starting directory path
            
        Returns:
            List of all file and folder metadata
            
        Note:
            For large accounts, consider using get_delta() instead for better performance.
        """
        all_items = []
        items = self.list_files(path)
        
        for item in items:
            # Add the item itself (file or folder)
            all_items.append(item)
            
            if 'folder' in item:
                # It's a folder, recurse into it
                folder_path = item['parentReference'].get('path', '').replace('/drive/root:', '') + '/' + item['name']
                all_items.extend(self.list_all_files(folder_path))
        
        return all_items
    
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
        except Exception as e:
            logger.debug(f"File not found on OneDrive: {remote_path}")
            return None
    
    @retry_on_failure(max_retries=3)
    def download_file(self, file_id: str, local_path: Path) -> Dict[str, Any]:
        """Download file from OneDrive with retry logic.
        
        Args:
            file_id: OneDrive file ID
            local_path: Local destination path
            
        Returns:
            File metadata including eTag
            
        Raises:
            Exception: If download fails after all retries
        """
        # Get metadata first
        metadata = self.get_file_metadata(file_id)
        
        endpoint = f"/me/drive/items/{file_id}/content"
        response = self._api_request('GET', endpoint, stream=True)
        
        # Create parent directories
        local_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Download with temporary file first for atomicity
        temp_path = local_path.with_suffix(local_path.suffix + '.tmp')
        try:
            # Use os.open with O_CREAT|O_EXCL to prevent TOCTOU symlink attacks
            fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            # Move to final location only if download succeeded
            temp_path.replace(local_path)
            
            logger.info(f"Downloaded: {local_path}")
            return metadata
            
        except Exception as e:
            # Clean up temp file on error
            if temp_path.exists():
                temp_path.unlink()
            raise
    
    @retry_on_failure(max_retries=3)
    def upload_file(self, local_path: Path, remote_path: str) -> Dict[str, Any]:
        """Upload file to OneDrive with retry logic.
        
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
        
        endpoint = f"/me/drive/root:/{remote_path}:/content"
        
        with open(local_path, 'rb') as f:
            headers = {'Content-Type': 'application/octet-stream'}
            response = self._api_request('PUT', endpoint, data=f, headers=headers)
        
        metadata = response.json()
        logger.info(f"Uploaded: {local_path} -> {remote_path}")
        return metadata
    
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
        except Exception as e:
            # Check if folder already exists
            if "already exists" in str(e).lower() or "name already exists" in str(e).lower():
                logger.debug(f"Folder already exists: {folder_path}")
                # Try to get existing folder metadata
                try:
                    endpoint = f"/me/drive/root:/{folder_path}"
                    response = self._api_request('GET', endpoint)
                    return response.json()
                except Exception as get_err:
                    logger.warning(f"Could not get metadata for existing folder: {get_err}")
            raise
    
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
