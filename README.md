# OneDrive Sync Client (ODSC)

A Ubuntu/GNOME-based sync client for Microsoft OneDrive (personal) that runs as a background service and provides a graphical interface for managing your cloud files.

## Features

- **Background Sync Service**: Automatically syncs files from your local directory to OneDrive
- **Event-Driven & Periodic Sync**: Monitors file changes in real-time and performs periodic full syncs
- **GNOME GTK Interface**: Native GUI showing OneDrive files with sync status
- **Selective Download**: View all OneDrive files without downloading them; download only when needed
- **OAuth2 Authentication**: Secure authentication with Microsoft OneDrive (no app registration required!)
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
sudo cp systemd/odsc.service /etc/systemd/user/

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
  - Local availability status (checkbox)

- **Download Files**: 
  1. Select one or more files that aren't local
  2. Click "Download Selected"
  3. Files will be downloaded to your sync directory

- **Refresh**: Click "Refresh" to update the file list from OneDrive

- **Automatic Upload**: Any files you add to the sync directory will be automatically uploaded to OneDrive

## Configuration

Configuration is stored in `~/.config/odsc/config.json`:

```json
{
  "sync_directory": "/home/user/OneDrive",
  "sync_interval": 300,
  "client_id": "your-azure-client-id",
  "auto_start": false
}
```

## File Sync Behavior

- **Upload**: Files created or modified in the sync directory are automatically uploaded to OneDrive
- **Download**: Files are NOT automatically downloaded; use the GUI to selectively download files
- **Delete**: Local deletions do NOT delete files from OneDrive (safety feature)

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
