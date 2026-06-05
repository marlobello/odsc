"""Tests for the Unix domain socket command server."""

import logging
import socket
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


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="SO_PEERCRED is Linux-specific")
def test_same_uid_connection_is_accepted(server, sock_dir):
    """Same-user Unix socket clients should be allowed to issue commands."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5.0)
    try:
        client.connect(str(sock_dir / ".odsc.sock"))
        client.sendall(b"VERSION\n")
        response = client.recv(128).decode("utf-8", errors="replace").strip()
    finally:
        client.close()

    assert response == "OK unknown"


def test_unknown_command_returns_error(server, sock_dir):
    """Unknown commands should return ERR."""
    srv, callback = server
    response = send_command(sock_dir, "BOGUS")
    assert response.startswith("ERR")
    callback.assert_not_called()


def test_sync_handler_exception_returns_generic_error(sock_dir, caplog):
    """Handler exceptions should be logged but hidden from socket clients."""
    callback = Mock(side_effect=RuntimeError("sensitive sync failure"))
    srv = CommandServer(sock_dir, callback)
    srv.start()
    try:
        with caplog.at_level(logging.ERROR, logger="odsc.command_socket"):
            response = send_command(sock_dir, "SYNC")
    finally:
        srv.stop()

    assert response == "ERR internal error"
    assert "sensitive sync failure" not in response
    assert "sensitive sync failure" in caplog.text


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
