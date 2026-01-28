#!/usr/bin/env python3
"""Command-line utility for ODSC."""

import argparse
import sys
import webbrowser
import http.server
import socketserver
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from odsc.config import Config
from odsc.onedrive_client import OneDriveClient


class AuthCallbackHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler for OAuth callback."""
    
    auth_code = None
    
    def do_GET(self):
        """Handle GET request for OAuth callback."""
        parsed = urlparse(self.path)
        if parsed.path == '/':
            params = parse_qs(parsed.query)
            if 'code' in params:
                AuthCallbackHandler.auth_code = params['code'][0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authentication successful!</h1>"
                                b"<p>You can close this window now.</p></body></html>")
            else:
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authentication failed!</h1></body></html>")
    
    def log_message(self, format, *args):
        """Suppress log messages."""
        pass


def cmd_auth(args):
    """Authenticate with OneDrive."""
    config = Config()
    
    # Get client ID (optional - will use default if not provided)
    client_id = args.client_id or config.client_id or None
    
    if args.client_id:
        config.set('client_id', args.client_id)
    
    # Create client and get auth URL
    client = OneDriveClient(client_id)
    auth_url = client.get_auth_url()
    
    print(f"Opening browser for authentication...")
    print(f"If browser doesn't open, visit: {auth_url}")
    webbrowser.open(auth_url)
    
    # Start local server for callback
    print("Waiting for authentication...")
    try:
        with socketserver.TCPServer(("", 8080), AuthCallbackHandler) as httpd:
            httpd.handle_request()
    except OSError as e:
        if e.errno == 98:  # Address already in use
            print(f"✗ Port 8080 is already in use. Please close other applications using this port.")
            return 1
        raise
    
    if AuthCallbackHandler.auth_code:
        try:
            token_data = client.exchange_code(AuthCallbackHandler.auth_code)
            config.save_token(token_data)
            print("✓ Authentication successful!")
            return 0
        except Exception as e:
            print(f"✗ Authentication failed: {e}")
            return 1
    else:
        print("✗ No authorization code received")
        return 1


def cmd_status(args):
    """Show sync status."""
    config = Config()
    
    print("OneDrive Sync Client Status")
    print("=" * 40)
    print(f"Sync Directory: {config.sync_directory}")
    print(f"Sync Interval: {config.sync_interval} seconds")
    print(f"Client ID: {config.client_id or '(using default)'}")
    
    # Check authentication
    token = config.load_token()
    if token:
        print("Authentication: ✓ Authenticated")
    else:
        print("Authentication: ✗ Not authenticated")
    
    # Check sync state
    state = config.load_state()
    num_files = len(state.get('files', {}))
    last_sync = state.get('last_sync', 'Never')
    
    print(f"Files Tracked: {num_files}")
    print(f"Last Sync: {last_sync}")
    
    return 0


def cmd_config(args):
    """Configure ODSC."""
    config = Config()
    
    if args.list:
        print("Current Configuration:")
        print("=" * 40)
        print(f"sync_directory = {config.sync_directory}")
        print(f"sync_interval = {config.sync_interval}")
        print(f"client_id = {config.client_id or '(not set)'}")
        return 0
    
    if args.set:
        for item in args.set:
            if '=' not in item:
                print(f"Error: Invalid format '{item}'. Use key=value")
                continue
            
            key, value = item.split('=', 1)
            
            # Type conversion
            if key == 'sync_interval':
                value = int(value)
            elif key == 'sync_directory':
                value = str(Path(value).expanduser())
            
            config.set(key, value)
            print(f"✓ Set {key} = {value}")
        
        return 0
    
    print("Use --list to view config or --set key=value to change config")
    return 0


def cmd_list(args):
    """List OneDrive files."""
    config = Config()
    
    # Check authentication
    token = config.load_token()
    if not token:
        print("Error: Not authenticated. Run 'odsc auth' first.")
        return 1
    
    # client_id is optional - will use default if not configured
    client_id = config.client_id or None
    
    client = OneDriveClient(client_id, token)
    
    try:
        print("Fetching files from OneDrive...")
        files = client.list_all_files()
        
        print(f"\nOneDrive Files ({len(files)} total):")
        print("=" * 60)
        
        for file in files:
            name = file.get('name', 'Unknown')
            size = file.get('size', 0)
            
            # Format size
            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f} MB"
            
            print(f"{name:40s} {size_str:>15s}")
        
        # Save updated token
        config.save_token(client.token_data)
        
        return 0
        
    except Exception as e:
        print(f"Error: {e}")
        return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='OneDrive Sync Client (ODSC) - Command-line utility'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Auth command
    auth_parser = subparsers.add_parser('auth', help='Authenticate with OneDrive')
    auth_parser.add_argument('--client-id', help='Custom Azure application client ID (optional, uses built-in default if not provided)')
    auth_parser.set_defaults(func=cmd_auth)
    
    # Status command
    status_parser = subparsers.add_parser('status', help='Show sync status')
    status_parser.set_defaults(func=cmd_status)
    
    # Config command
    config_parser = subparsers.add_parser('config', help='Configure ODSC')
    config_parser.add_argument('--list', action='store_true', help='List configuration')
    config_parser.add_argument('--set', nargs='+', help='Set config (key=value)')
    config_parser.set_defaults(func=cmd_config)
    
    # List command
    list_parser = subparsers.add_parser('list', help='List OneDrive files')
    list_parser.set_defaults(func=cmd_list)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
