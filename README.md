# OneDrive Sync Client (ODSC)

A Ubuntu/GNOME-based sync client for Microsoft OneDrive (personal) that runs as a background service and provides a graphical interface for managing your cloud files.

## Features

- **Background Sync Service**: Automatically syncs files from your local directory to OneDrive
- **Event-Driven & Periodic Sync**: Monitors file changes in real-time and performs periodic full syncs
- **GNOME GTK Interface**: Native GUI showing OneDrive files with sync status
- **Selective Download**: View all OneDrive files without downloading them; download only when needed
- **OAuth2 Authentication**: Secure authentication with Microsoft OneDrive
- **Systemd Integration**: Run as a system service with auto-start capability

## Prerequisites

- Ubuntu 20.04+ (or any Linux distribution with GNOME 3.x+)
- Python 3.8 or higher
- GTK 3.0 development libraries
- Microsoft account with OneDrive (personal or Microsoft 365 Family subscription)

## Installation

### 1. Install System Dependencies

```bash
sudo apt-get update
sudo apt-get install python3 python3-pip python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-dbus python3-requests python3-watchdog python3-dateutil
```

### 2. Clone Repository

```bash
git clone https://github.com/marlobello/odsc.git
cd odsc
```

### 3. Install ODSC Package

**Option A: Using the install script (recommended)**:
```bash
./install.sh
```
This installs all dependencies via apt and only uses pip for ODSC entry points.

**Option B: Manual installation**:

After installing system dependencies, install ODSC:

```bash
pip3 install --user --break-system-packages -e . --no-deps
```

The `--no-deps` flag ensures only ODSC is installed via pip, using system packages for dependencies.

## Usage

### First-Time Setup

1. **Launch the GUI**:
   ```bash
   odsc-gui
   ```

2. **Authenticate**:
   - Click "Authenticate" in the toolbar
   - A browser window will open for Microsoft authentication
   - Log in with your Microsoft account
   - Authorize the application
   - Return to the GUI (authentication will complete automatically)
   
   **Note**: ODSC uses a built-in Azure app registration. If you prefer to use your own, you can specify a custom client ID in the settings.

3. **Configure Settings**:
   - Click "Settings" to configure sync directory and interval
   - Default sync directory: `~/OneDrive`
   - Default sync interval: 300 seconds (5 minutes)

### Running the Sync Daemon

After authentication, start the background sync daemon:

```bash
odsc-daemon
```

Or install as a systemd service:

```bash
# Copy service file
mkdir -p ~/.config/systemd/user/
cp systemd/odsc.service ~/.config/systemd/user/

# Enable and start service
systemctl --user enable odsc
systemctl --user start odsc

# Check status
systemctl --user status odsc
```

### Using the GUI

The GUI provides the following features:

- **File List**: Shows all files in your OneDrive with:
  - File name
  - Size
  - Last modified date
  - Local availability status (checkbox indicates if file has local copy)

- **Keep Local Copy**: 
  1. Select one or more files that don't have local copies
  2. Click "Keep Local Copy"
  3. Files will be downloaded to your sync directory
  4. Files will now be automatically kept in sync

- **Remove Local Copy**:
  1. Select one or more files that have local copies
  2. Click "Remove Local Copy"
  3. Local files deleted, but remain on OneDrive
  4. Files stop being automatically synced
  5. Can be re-downloaded anytime

- **Refresh**: Click "Refresh" to update the file list from OneDrive

- **Automatic Upload**: Any new or modified files in your sync directory are automatically uploaded to OneDrive

## Configuration

Configuration is stored in `~/.config/odsc/config.json`:

```json
{
  "sync_directory": "/home/user/OneDrive",
  "sync_interval": 300,
  "client_id": "",
  "auto_start": false,
  "log_level": "INFO"
}
```

### Configuration Options

- **sync_directory**: Local directory for OneDrive files
- **sync_interval**: Seconds between sync checks (default: 300)
- **client_id**: Custom Azure client ID (optional, uses default if empty)
- **auto_start**: Auto-start with systemd (default: false)
- **log_level**: Logging verbosity - DEBUG, INFO, WARNING, ERROR, CRITICAL (default: INFO)

### Logging

ODSC includes comprehensive logging for debugging. Logs are written to:
- **Console**: When running GUI or daemon
- **File**: `~/.config/odsc/odsc.log` (with automatic rotation)

**Log Rotation:**
- Maximum log file size: 10 MB
- Backup files kept: 5 (odsc.log.1, odsc.log.2, etc.)
- Total maximum log storage: ~50 MB
- Old logs automatically deleted when limit reached

**Log Levels:**
- `DEBUG`: Detailed diagnostic information (API calls, tokens, responses)
- `INFO`: General informational messages (default)
- `WARNING`: Warning messages for potential issues
- `ERROR`: Error messages for failures
- `CRITICAL`: Critical failures

**To enable debug logging:**

1. Edit `~/.config/odsc/config.json` and set `"log_level": "DEBUG"`
2. Or set environment variable: `export ODSC_LOG_LEVEL=DEBUG`
3. Restart the GUI or daemon

**View logs:**
```bash
# Real-time log viewing
tail -f ~/.config/odsc/odsc.log

# View last 50 lines
tail -n 50 ~/.config/odsc/odsc.log

# Search for errors
grep ERROR ~/.config/odsc/odsc.log
```

## File Sync Behavior

ODSC is designed with a **safety-first** approach to prevent accidental data loss. Understanding how sync works in different scenarios is important for proper use.

### Upload (Automatic)
- **New local files**: Automatically uploaded to OneDrive
- **Modified local files**: Changes detected in real-time and uploaded
- **Detection methods**: 
  - Real-time file system monitoring (Watchdog)
  - Periodic full scans (every 5 minutes by default)

### Download (Selective, Then Synced)
- **New OneDrive files**: NOT automatically downloaded
  - Appear in GUI file list but remain cloud-only
  - Use "Keep Local Copy" button to download specific files
  - This prevents unwanted downloads and saves local storage
- **Downloaded files**: Automatically kept in sync
  - Marked as `downloaded` in sync state
  - Remote updates are automatically downloaded
  - Local changes are automatically uploaded

### "Keep Local Copy" Button
- Downloads file from OneDrive to your sync directory
- Marks file as actively synced
- File will now receive automatic updates from OneDrive
- Local changes will be automatically uploaded

### "Remove Local Copy" Button
- **Deletes file from local storage only**
- **File remains on OneDrive** (safety feature)
- Marks file as not downloaded in sync state
- File stops being automatically synced
- Can be re-downloaded anytime using "Keep Local Copy"

### Remote Deletions (File Deleted in OneDrive)
When a file you've downloaded is deleted from OneDrive:
- **Local copy moved to system recycle bin/trash**
  - Uses OS trash system (recoverable via system trash folder)
  - Does NOT permanently delete
  - Prevents accidental data loss from remote deletions
- **Removed from sync state**
- If trash fails, falls back to permanent deletion with warning logged

### Local Deletions (File Deleted Locally)
When you delete a file from your local sync directory:
- **File remains on OneDrive** (safety feature)
- File is NOT deleted from cloud storage
- If file was being synced:
  - Marked as locally deleted in sync state
  - Will not be re-downloaded automatically
  - Must use "Keep Local Copy" to download again
- **Rationale**: Protects against accidental local deletions

### Conflict Resolution
When both local and remote versions of a file are modified:
- **Both versions are preserved**:
  - Local version remains unchanged at original path
  - Remote version downloaded as `filename.conflict`
- **User must manually resolve**:
  - Review both versions
  - Keep the desired version
  - Delete or rename the other
  - System makes no assumptions about which version is "correct"

### Edge Cases

#### New File Already Exists Remotely
If you add a file locally that already exists on OneDrive (same name/path):
- If sizes match: Assumed to be same file, synced normally
- If sizes differ: Treated as conflict, creates `.conflict` file

#### Remote File Modified After "Remove Local Copy"
If you removed local copy of a file, then it's updated on OneDrive:
- File remains cloud-only (NOT automatically re-downloaded)
- Must manually "Keep Local Copy" to get updated version
- Prevents unwanted downloads of files you've chosen not to sync

#### File Deleted Both Locally and Remotely
- Both deletions honored (file gone from both places)
- No conflict or recovery needed
- Sync state cleaned up automatically

#### Multiple Rapid Changes
- Local changes are batched and debounced before upload
- Reduces API calls and handles save-spam from applications
- Final state is always synced correctly

## Troubleshooting

### Authentication Issues

If authentication fails:
1. Ensure port 8080 is not in use by another application
2. Check your internet connection
3. Try authenticating again with `odsc auth` or through the GUI

### Sync Not Working

1. Check daemon is running: `systemctl --user status odsc`
2. Check logs: `journalctl --user -u odsc -f`
3. Verify token exists: `ls ~/.config/odsc/.onedrive_token`

### GUI Won't Start

1. Ensure GTK libraries are installed: `sudo apt-get install gir1.2-gtk-3.0`
2. Try running from terminal to see error messages: `odsc-gui`

### Application Not Appearing in Menu (GNOME/Ubuntu)

After installation, the application may not immediately appear in your application menu due to GNOME Shell caching. Try these solutions:

**Option 1: Search for the application**
1. Press Super key (Windows key) to open Activities
2. Type "OneDrive" in the search box
3. The application should appear and can be launched

**Option 2: Refresh the desktop entries** (Run the provided script)
```bash
./refresh-desktop-entry.sh
```

**Option 3: Restart GNOME Shell** (X11 only, not Wayland)
1. Press `Alt+F2`
2. Type `r` and press Enter

**Option 4: Log out and log back in**

**Option 5: Launch directly from terminal**
```bash
odsc-gui
```

Once launched, you can right-click the application icon and select "Add to Favorites" to pin it to your dock.

## Development

### Project Structure

```
odsc/
├── src/odsc/
│   ├── __init__.py
│   ├── config.py          # Configuration management
│   ├── onedrive_client.py # OneDrive API client
│   ├── daemon.py          # Background sync daemon
│   └── gui.py             # GNOME GTK interface
├── systemd/
│   └── odsc.service       # Systemd service file
├── desktop/
│   └── odsc.desktop       # Desktop application entry
├── requirements.txt
├── setup.py
└── README.md
```

### Running from Source

```bash
# Run daemon
python3 -m odsc.daemon

# Run GUI
python3 -m odsc.gui
```

## License

MIT License - See [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Acknowledgments

- Microsoft Graph API for OneDrive integration
- Watchdog library for file system monitoring
- PyGObject for GTK bindings
