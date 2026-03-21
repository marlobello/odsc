"""Shared OAuth callback HTTP handler for ODSC CLI and GUI."""

import http.server
from urllib.parse import urlparse, parse_qs


class AuthCallbackHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler for the local OAuth redirect URI.

    Both the CLI (``cli.py``) and the GUI (``gui/auth_handler.py``) import
    this class so any change to the OAuth flow only needs to be made once.

    Class attributes are used instead of instance attributes so that the
    TCPServer machinery can retrieve the captured code/state after the
    request has been handled.
    """

    auth_code = None
    state = None  # For CSRF validation

    def do_GET(self) -> None:
        """Handle the GET request that OneDrive redirects to after auth."""
        parsed = urlparse(self.path)
        if parsed.path == '/':
            params = parse_qs(parsed.query)
            if 'code' in params:
                AuthCallbackHandler.auth_code = params['code'][0]
                AuthCallbackHandler.state = params.get('state', [None])[0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>Authentication successful!</h1>"
                    b"<p>You can close this window now.</p></body></html>"
                )
            else:
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>Authentication failed!</h1></body></html>"
                )

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """Suppress default access-log noise."""
