# Copilot Instructions for ODSC

## Build & Test

```bash
# Install for development
pip install -e . --no-deps --no-build-isolation

# Run full test suite
python3 -m pytest tests/ -q

# Run a single test file
python3 -m pytest tests/test_config.py -q

# Run a single test function
python3 -m pytest tests/test_config.py::test_config_initialization -q
```

CI requires system GTK/D-Bus packages (`python3-gi`, `python3-dbus`). In headless CI, install `keyrings.alt` for a file-based keyring backend. PyGObject and dbus-python are excluded from pip install and provided via system packages.

## Architecture

ODSC is a two-process Linux application: a **background sync daemon** (`odsc-daemon`) and a **GTK 3 GUI** (`odsc-gui`). They share configuration and state but run independently.

- **Daemon** (`src/odsc/daemon.py`): Watchdog-based file watcher + periodic full sync. Uploads local changes automatically; downloads only files the user has opted into ("selective sync"). Communicates status via a Unix command socket (`command_socket.py`).
- **GUI** (`src/odsc/gui/`): Multi-file GTK application. UI runs on the main GTK thread; all API/network calls happen on background threads using `GLib.idle_add()` for thread-safe UI updates.
- **OneDrive Client** (`src/odsc/onedrive_client.py`): Microsoft Graph API wrapper handling OAuth2, token refresh, uploads, downloads, and delta queries.
- **State backends** (`src/odsc/backends/`): Pluggable storage for sync state with a `StateBackend` ABC, a JSON backend, and a SQLite backend with migration support.
- **Services** (`src/odsc/services/file_cache_service.py`): Caches OneDrive file metadata.

Entry points are defined in `setup.py` under `console_scripts`: `odsc`, `odsc-daemon`, `odsc-gui`, `odsc-reset-local`.

## Key Conventions

- **Package layout**: All source is under `src/odsc/`. Tests are in `tests/` at the repo root. The project uses `find_packages(where="src")` with `package_dir={"": "src"}`.
- **Python version**: 3.8+ compatibility required (no walrus operator, no `match` statements).
- **Safety-first sync design**: Local deletions are *never* propagated to OneDrive. Remote deletions move local files to trash via `send2trash`. Conflicts preserve both versions (`.conflict` suffix).
- **Optional GTK imports**: System tray and GTK modules use try/except imports so the daemon can run headless without a display server.
- **Path security**: Use `sanitize_onedrive_path()` and `validate_sync_path()` from `path_utils.py` when constructing file paths. A `SecurityError` is raised for path traversal attempts.
- **Token encryption**: OAuth tokens are encrypted via the `cryptography` library and stored through the system keyring (`keyring` package).
- **Retry logic**: Network calls use `tenacity` for exponential backoff with jitter.
- **Version**: Stored in the `VERSION` file at the repo root (read by `setup.py`).
