"""Shared OAuth callback HTTP handler for ODSC CLI and GUI."""

import logging
import http.server
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)


class AuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for the local OAuth redirect URI.

    Both the CLI (``cli.py``) and the GUI (``gui/auth_handler.py``) import
    this class so any change to the OAuth flow only needs to be made once.

    Class attributes are used instead of instance attributes so that the
    TCPServer machinery can retrieve the captured code/state after the
    request has been handled.
    """

    auth_code = None
    state = None  # For CSRF validation

    @classmethod
    def reset(cls) -> None:
        """Clear any previously captured OAuth callback data."""
        cls.auth_code = None
        cls.state = None

    def do_GET(self) -> None:
        """Handle the GET request that OneDrive redirects to after auth."""
        logger.info(f"OAuth callback received request: {self.path}")
        parsed = urlparse(self.path)
        if parsed.path == '/':
            params = parse_qs(parsed.query)
            if 'code' in params:
                AuthCallbackHandler.auth_code = params['code'][0]
                AuthCallbackHandler.state = params.get('state', [None])[0]
                logger.info("OAuth callback: authorization code received")
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>Authentication successful!</h1>"
                    b"<p>You can close this window now.</p></body></html>"
                )
            elif 'error' in params:
                error = params['error'][0]
                desc = params.get('error_description', [''])[0]
                logger.error(f"OAuth callback error: {error} - {desc}")
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(
                    f"<html><body><h1>Authentication failed</h1><p>{error}: {desc}</p></body></html>".encode()
                )
            else:
                logger.warning(f"OAuth callback: no code or error in params: {params}")
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>Authentication failed!</h1></body></html>"
                )
        else:
            logger.debug(f"OAuth callback: ignoring request to {parsed.path}")
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """Route access logs through the application logger."""
        logger.debug(format, *args)
