"""DaemonController — centralized management of the ODSC systemd service.

All GUI code that needs to start/stop/restart/query the daemon imports this
class instead of duplicating subprocess + systemctl boilerplate.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)

_SERVICE = "odsc.service"
_SERVICE_SHORT = "odsc"


class DaemonController:
    """Manages the ODSC systemd user service lifecycle.

    All methods return a ``(success: bool, message: str)`` tuple so callers
    can decide how to present feedback without this class touching any GTK
    widgets.
    """

    # --------------------------------------------------------------------- #
    # Status                                                                  #
    # --------------------------------------------------------------------- #

    def is_running(self) -> bool:
        """Return True if the daemon service is currently active."""
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", _SERVICE],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    # --------------------------------------------------------------------- #
    # Lifecycle                                                               #
    # --------------------------------------------------------------------- #

    def start(self) -> tuple[bool, str]:
        """Start the daemon service.

        Returns:
            (success, human-readable message)
        """
        return self._run(["systemctl", "--user", "start", _SERVICE_SHORT], timeout=10)

    def stop(self) -> tuple[bool, str]:
        """Stop the daemon service.

        Returns:
            (success, human-readable message)
        """
        return self._run(["systemctl", "--user", "stop", _SERVICE_SHORT], timeout=10)

    def restart(self) -> tuple[bool, str]:
        """Restart the daemon service (start it if not running).

        Returns:
            (success, human-readable message)
        """
        if self.is_running():
            return self._run(
                ["systemctl", "--user", "restart", _SERVICE_SHORT], timeout=10
            )
        else:
            return self.start()

    # --------------------------------------------------------------------- #
    # Internal                                                                #
    # --------------------------------------------------------------------- #

    def _run(self, cmd: list[str], timeout: int) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0:
                logger.info(f"daemon_controller: {' '.join(cmd)} succeeded")
                return True, ""
            else:
                msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"daemon_controller: {' '.join(cmd)} failed: {msg}")
                return False, msg
        except subprocess.TimeoutExpired:
            msg = f"Timed out after {timeout}s running {' '.join(cmd)}"
            logger.error(f"daemon_controller: {msg}")
            return False, msg
        except FileNotFoundError:
            msg = (
                "systemctl not found — please manage the daemon manually:\n"
                f"  systemctl --user start {_SERVICE_SHORT}"
            )
            logger.error(f"daemon_controller: systemctl not found")
            return False, msg
        except Exception as exc:
            msg = str(exc)
            logger.error(f"daemon_controller: unexpected error: {msg}", exc_info=True)
            return False, msg
