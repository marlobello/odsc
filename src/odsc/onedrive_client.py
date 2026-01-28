#!/usr/bin/env python3
"""OneDrive API client for ODSC."""

import logging
import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests


logger = logging.getLogger(__name__)


class OneDriveClient:
    """Client for interacting with Microsoft OneDrive API."""
    
    # Microsoft Graph API endpoints
    # Using /consumers/ endpoint for personal Microsoft accounts (OneDrive Consumer)
    AUTH_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
    TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
    API_BASE = "https://graph.microsoft.com/v1.0"
    REDIRECT_URI = "http://localhost:8080"
    SCOPES = "files.readwrite offline_access"
    
    # Default public client ID for ODSC
    # Registered Azure application for OneDrive Consumer (personal accounts)
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
    
    def get_auth_url(self) -> str:
        """Get OAuth2 authorization URL.
        
        Returns:
            Authorization URL
        """
        params = {
            'client_id': self.client_id,
            'scope': self.SCOPES,
            'response_type': 'code',
            'redirect_uri': self.REDIRECT_URI,
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"
    
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
        
        response = requests.post(self.TOKEN_URL, data=data)
        response.raise_for_status()
        
        self.token_data = response.json()
        self.token_data['expires_at'] = time.time() + self.token_data.get('expires_in', 3600)
        return self.token_data
    
    def refresh_token(self) -> Dict[str, Any]:
        """Refresh access token using refresh token.
        
        Returns:
            New token data
        """
        if 'refresh_token' not in self.token_data:
            raise ValueError("No refresh token available")
        
        data = {
            'client_id': self.client_id,
            'refresh_token': self.token_data['refresh_token'],
            'grant_type': 'refresh_token',
        }
        
        response = requests.post(self.TOKEN_URL, data=data)
        response.raise_for_status()
        
        self.token_data = response.json()
        self.token_data['expires_at'] = time.time() + self.token_data.get('expires_in', 3600)
        return self.token_data
    
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
    
    def list_files(self, path: str = "/") -> List[Dict[str, Any]]:
        """List files in OneDrive directory.
        
        Args:
            path: Directory path (default: root)
            
        Returns:
            List of file/folder metadata
        """
        if path == "/":
            endpoint = "/me/drive/root/children"
        else:
            # Remove leading/trailing slashes
            path = path.strip("/")
            endpoint = f"/me/drive/root:/{path}:/children"
        
        response = self._api_request('GET', endpoint)
        data = response.json()
        return data.get('value', [])
    
    def list_all_files(self, path: str = "/") -> List[Dict[str, Any]]:
        """Recursively list all files in OneDrive.
        
        Args:
            path: Starting directory path
            
        Returns:
            List of all file metadata
        """
        all_files = []
        items = self.list_files(path)
        
        for item in items:
            if 'folder' in item:
                # It's a folder, recurse into it
                folder_path = item['parentReference'].get('path', '').replace('/drive/root:', '') + '/' + item['name']
                all_files.extend(self.list_all_files(folder_path))
            else:
                # It's a file
                all_files.append(item)
        
        return all_files
    
    def download_file(self, file_id: str, local_path: Path) -> None:
        """Download file from OneDrive.
        
        Args:
            file_id: OneDrive file ID
            local_path: Local destination path
        """
        endpoint = f"/me/drive/items/{file_id}/content"
        response = self._api_request('GET', endpoint, stream=True)
        
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        logger.info(f"Downloaded: {local_path}")
    
    def upload_file(self, local_path: Path, remote_path: str) -> Dict[str, Any]:
        """Upload file to OneDrive.
        
        Args:
            local_path: Local file path
            remote_path: Remote destination path
            
        Returns:
            Upload response metadata
        """
        # Remove leading slash
        remote_path = remote_path.lstrip('/')
        
        endpoint = f"/me/drive/root:/{remote_path}:/content"
        
        with open(local_path, 'rb') as f:
            headers = {'Content-Type': 'application/octet-stream'}
            response = self._api_request('PUT', endpoint, data=f, headers=headers)
        
        logger.info(f"Uploaded: {local_path} -> {remote_path}")
        return response.json()
    
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
