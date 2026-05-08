"""Unix domain socket for IPC between GUI and daemon.

Replaces the file-based force-sync signal with an event-driven,
ownership-verified Unix socket. The daemon listens; the GUI (or CLI)
connects and sends single-line commands.

Supported commands:
  SYNC    — trigger an immediate full sync
  VERSION — return the daemon's version string
"""

import logging
import os
import socket
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_SOCKET_FILENAME = ".odsc.sock"
_BUFFER_SIZE = 128


def socket_path(config_dir: Path) -> Path:
    """Return the canonical socket path for a config directory."""
    return config_dir / _SOCKET_FILENAME


class CommandServer:
    """Listens on a Unix socket for commands from the GUI/CLI."""

    def __init__(self, config_dir: Path, on_sync_requested: callable,
                 version: str = "unknown"):
        self._sock_path = socket_path(config_dir)
        self._on_sync = on_sync_requested
        self._version = version
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        """Bind socket and start listener thread."""
        # Clean up stale socket from a previous crash
        self._sock_path.unlink(missing_ok=True)

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self._sock_path))
        # Restrict socket to owner only
        os.chmod(self._sock_path, 0o700)
        self._server.listen(4)
        self._server.settimeout(1.0)
        self._running = True

        self._thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="command-socket"
        )
        self._thread.start()
        logger.info(f"Command socket listening at {self._sock_path}")

    def stop(self) -> None:
        """Shut down listener and clean up socket file."""
        self._running = False
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        self._sock_path.unlink(missing_ok=True)
        logger.debug("Command socket stopped")

    def _accept_loop(self) -> None:
        """Accept connections and dispatch commands."""
        while self._running:
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                data = conn.recv(_BUFFER_SIZE).decode("utf-8", errors="replace").strip()
                response = self._handle(data)
                conn.sendall(response.encode("utf-8"))
            except Exception as e:
                logger.debug(f"Command socket client error: {e}")
            finally:
                conn.close()

    def _handle(self, command: str) -> str:
        """Dispatch a command string and return a response."""
        if command == "SYNC":
            logger.info("Force sync requested via command socket")
            try:
                self._on_sync()
                return "OK\n"
            except Exception as e:
                logger.error(f"Error handling SYNC command: {e}")
                return f"ERR {e}\n"
        elif command == "VERSION":
            return f"OK {self._version}\n"
        else:
            logger.warning(f"Unknown command received: {command!r}")
            return "ERR unknown command\n"


def send_command(config_dir: Path, command: str, timeout: float = 5.0) -> str:
    """Send a command to the daemon and return the response.

    This is the client-side helper used by the GUI / CLI.

    Args:
        config_dir: Config directory where the socket lives.
        command: Command string (e.g. "SYNC").
        timeout: Seconds to wait for response.

    Returns:
        Response string from daemon.

    Raises:
        ConnectionError: If daemon is not running or socket unavailable.
    """
    path = socket_path(config_dir)
    if not path.exists():
        raise ConnectionError("Daemon is not running (socket not found)")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        sock.sendall(f"{command}\n".encode("utf-8"))
        return sock.recv(_BUFFER_SIZE).decode("utf-8", errors="replace").strip()
    finally:
        sock.close()
