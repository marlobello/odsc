"""Helpers for classifying and sanitizing application errors."""

from __future__ import annotations

import logging
from typing import Optional

import requests

from .path_utils import SecurityError


def get_http_status(exc: BaseException) -> Optional[int]:
    """Return the HTTP status code attached to a requests HTTPError, if any."""
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def is_transient_error(exc: BaseException) -> bool:
    """Return True when *exc* represents a temporary failure worth retrying."""
    if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True

    status = get_http_status(exc)
    return status is not None and status >= 500


def get_log_level(exc: BaseException) -> int:
    """Map exceptions to an appropriate log level."""
    return logging.WARNING if is_transient_error(exc) else logging.ERROR


def log_exception(
    logger: logging.Logger,
    message: str,
    exc: BaseException,
    *,
    exc_info: bool = False,
) -> None:
    """Log *exc* using warning for transient failures and error otherwise."""
    level = get_log_level(exc)
    logger.log(
        level,
        f"{message}: {exc}",
        exc_info=exc_info and level >= logging.ERROR,
    )


def user_friendly_error(action: str, exc: BaseException, *, item_type: str = "item") -> str:
    """Return a sanitized GUI-safe error message for *exc*."""
    if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return (
            f"Could not {action} because OneDrive is temporarily unreachable. "
            "Please check your connection and try again."
        )

    status = get_http_status(exc)
    if status in (401, 403):
        return "Your OneDrive session has expired or access was denied. Please sign in again."
    if status == 404:
        return f"The selected {item_type} is no longer available."
    if status is not None and status >= 500:
        return f"OneDrive is temporarily unavailable, so {action} could not be completed."
    if status is not None and 400 <= status < 500:
        return f"OneDrive rejected the request to {action}. Please review the item and try again."

    if isinstance(exc, SecurityError):
        return "The selected path is invalid."
    if isinstance(exc, PermissionError):
        return f"Permission was denied while trying to {action}."
    if isinstance(exc, FileNotFoundError):
        return f"The selected {item_type} could not be found."
    if isinstance(exc, ValueError):
        return f"The data needed to {action} was invalid."

    return f"Could not {action}. Please try again or check the logs for details."
