"""Filesystem helpers."""

import os
from pathlib import Path


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write *data* to *path* atomically using a temp file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_name(f"{path.name}.tmp")
    dir_fd = os.open(str(path.parent), os.O_RDONLY)

    try:
        fd = None
        try:
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            with os.fdopen(fd, "wb") as tmp_file:
                fd = None
                tmp_file.write(data)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())

            os.replace(str(tmp_path), str(path))
            os.fsync(dir_fd)
        finally:
            if fd is not None:
                os.close(fd)
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
    finally:
        os.close(dir_fd)
