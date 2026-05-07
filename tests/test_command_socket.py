"""Tests for the Unix domain socket command server."""

import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from odsc.command_socket import CommandServer, send_command


@pytest.fixture
def sock_dir(tmp_path):
    return tmp_path


@pytest.fixture
def server(sock_dir):
    callback = Mock()
    srv = CommandServer(sock_dir, callback)
    srv.start()
    yield srv, callback
    srv.stop()


def test_sync_command_triggers_callback(server, sock_dir):
    """SYNC command should call the on_sync callback and return OK."""
    srv, callback = server
    response = send_command(sock_dir, "SYNC")
    assert response == "OK"
    callback.assert_called_once()


def test_unknown_command_returns_error(server, sock_dir):
    """Unknown commands should return ERR."""
    srv, callback = server
    response = send_command(sock_dir, "BOGUS")
    assert response.startswith("ERR")
    callback.assert_not_called()


def test_send_command_raises_when_no_daemon(tmp_path):
    """send_command should raise ConnectionError if socket doesn't exist."""
    with pytest.raises(ConnectionError, match="not running"):
        send_command(tmp_path, "SYNC")


def test_socket_cleaned_up_on_stop(sock_dir):
    """Socket file should be removed when server stops."""
    srv = CommandServer(sock_dir, Mock())
    srv.start()
    sock_path = sock_dir / ".odsc.sock"
    assert sock_path.exists()
    srv.stop()
    assert not sock_path.exists()
