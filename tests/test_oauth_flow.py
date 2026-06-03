#!/usr/bin/env python3
"""Tests for OAuth callback state handling."""

from types import SimpleNamespace

from odsc import cli
from odsc.oauth_callback import AuthCallbackHandler
from odsc.onedrive_client import OneDriveClient


def test_auth_callback_handler_reset_clears_stale_values():
    """Callback handler state should never leak across auth attempts."""
    AuthCallbackHandler.auth_code = "stale-code"
    AuthCallbackHandler.state = "stale-state"

    AuthCallbackHandler.reset()

    assert AuthCallbackHandler.auth_code is None
    assert AuthCallbackHandler.state is None


def test_get_auth_url_uses_provided_state():
    """Explicit auth state should be preserved for callback validation."""
    client = OneDriveClient()

    auth_url = client.get_auth_url("expected-state")

    assert "state=expected-state" in auth_url
    assert client.state == "expected-state"
    assert client.validate_state("expected-state") is True
    assert client.state is None


def test_cmd_auth_rejects_state_mismatch(monkeypatch, capsys):
    """CLI auth should reject callbacks whose state differs from the request."""
    saved_tokens = []

    class FakeConfig:
        client_id = ""

        def set(self, key, value):
            self.client_id = value

        def save_token(self, token_data):
            saved_tokens.append(token_data)

    class FakeClient:
        def __init__(self, client_id):
            self.client_id = client_id
            self.state = None

        def get_auth_url(self, state=None):
            self.state = state
            return "https://example.test/auth"

        def validate_state(self, received_state):
            is_valid = self.state == received_state
            self.state = None
            return is_valid

        def exchange_code(self, code):
            raise AssertionError("exchange_code should not run for invalid state")

    class FakeServer:
        def __init__(self, address, handler_cls):
            self.handler_cls = handler_cls
            self.timeout = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def handle_request(self):
            self.handler_cls.auth_code = "fresh-code"
            self.handler_cls.state = "wrong-state"

    monkeypatch.setattr(cli, "Config", FakeConfig)
    monkeypatch.setattr(cli, "OneDriveClient", FakeClient)
    monkeypatch.setattr(cli.socketserver, "TCPServer", FakeServer)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda _: "expected-state")

    result = cli.cmd_auth(SimpleNamespace(client_id=None))

    assert result == 1
    assert saved_tokens == []
    assert AuthCallbackHandler.auth_code is None
    assert AuthCallbackHandler.state is None
    assert "Invalid state parameter" in capsys.readouterr().out


def test_cmd_auth_clears_stale_handler_state_before_waiting(monkeypatch, capsys):
    """CLI auth should not reuse a stale auth code when no new callback arrives."""
    AuthCallbackHandler.auth_code = "stale-code"
    AuthCallbackHandler.state = "stale-state"

    class FakeConfig:
        client_id = ""

        def set(self, key, value):
            self.client_id = value

        def save_token(self, token_data):
            raise AssertionError("save_token should not run without a new callback")

    class FakeClient:
        def __init__(self, client_id):
            self.client_id = client_id
            self.state = None

        def get_auth_url(self, state=None):
            self.state = state
            return "https://example.test/auth"

        def validate_state(self, received_state):
            raise AssertionError("validate_state should not run without a callback")

        def exchange_code(self, code):
            raise AssertionError("exchange_code should not run without a callback")

    class FakeServer:
        def __init__(self, address, handler_cls):
            self.handler_cls = handler_cls
            self.timeout = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def handle_request(self):
            return None

    call_count = [0]
    base_time = __import__('time').time()
    def fake_time():
        # First call sets the deadline; subsequent calls exceed it
        call_count[0] += 1
        if call_count[0] <= 1:
            return base_time
        return base_time + 400

    monkeypatch.setattr(cli, "Config", FakeConfig)
    monkeypatch.setattr(cli, "OneDriveClient", FakeClient)
    monkeypatch.setattr(cli.socketserver, "TCPServer", FakeServer)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda _: "expected-state")
    monkeypatch.setattr(cli.time, "time", fake_time)

    result = cli.cmd_auth(SimpleNamespace(client_id=None))

    assert result == 1
    assert AuthCallbackHandler.auth_code is None
    assert AuthCallbackHandler.state is None
    assert "No authorization code received" in capsys.readouterr().out
