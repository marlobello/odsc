# OneDrive Sync Client (ODSC)

A lightweight Linux sync client for Microsoft OneDrive that runs as a background service with a GTK graphical interface.

## Features

- **Background Sync**: Automatically syncs files between local directory and OneDrive
- **Real-time Monitoring**: Detects file changes instantly using filesystem events
- **Native GTK Interface**: Clean GNOME-style GUI for managing files
- **Selective Sync**: Choose which files to download and keep in sync
- **Secure Authentication**: OAuth2 with encrypted token storage
- **Force Sync**: Manual sync trigger via GUI menu
- **Folder Sync**: Bidirectional folder syncing including empty folders

## Requirements

- Linux with GTK 3.0+ (Ubuntu 20.04+, Fedora 33+, etc.)
- Python 3.8 or higher
- Microsoft account with OneDrive

## Quick Installation

**One command to install everything:**

```bash
./install.sh
```

The install script will:
1. ✅ Check Python version (requires 3.8+)
2. ✅ Install all system dependencies (GTK, D-Bus, Python packages)
3. ✅ Install ODSC application
4. ✅ Create default sync directory (`~/OneDrive`)
5. ✅ Install application icon
6. ✅ Set up systemd service (optional)
7. ✅ Add GUI to application menu (optional)

**That's it!** The script handles everything automatically.

## First-Time Setup

### 1. Launch the Application

Find "OneDrive Sync Client" in your applications menu, or run:

```bash
odsc-gui
```

### 2. Authenticate with Microsoft

1. Click **Authentication → Login** in the menu
2. Your browser will open for Microsoft login
3. Sign in with your Microsoft account
4. Authorize the application
5. Return to the GUI (authentication completes automatically)

### 3. Start Syncing

The background sync daemon should already be running if you enabled the systemd service during installation.

Check daemon status:
```bash
systemctl --user status odsc
```

Start daemon manually (if not using systemd):
```bash
odsc-daemon
```

## How It Works

### Automatic Upload
- **New or modified local files** → Automatically uploaded to OneDrive
- **Detected via:** Real-time filesystem monitoring + periodic scans

### Selective Download
- **New OneDrive files** → Appear in GUI but NOT auto-downloaded
- **To download:** Select files and click "Keep Local Copy"
- **Once downloaded:** File stays in sync automatically

### Manual Sync Trigger
- Click **Settings → Force Sync Now** to trigger immediate sync

### Safety Features
- **Remote deletions** → Local files moved to trash (not permanently deleted)
- **Local deletions** → Files remain on OneDrive (prevents accidental loss)
- **Conflicts** → Both versions kept (`.conflict` file created)

## Configuration

Settings are stored in `~/.config/odsc/config.json` and can be changed via the GUI:

**Settings → Preferences:**
- **Sync Directory**: Where files are stored locally (default: `~/OneDrive`)
- **Sync Interval**: How often to check for changes (default: 300 seconds)
- **Log Level**: Verbosity of logging (INFO, DEBUG, WARNING, ERROR)

## Managing Files

### Keep Local Copy
1. Select files without local copies in the GUI
2. Click **"Keep Local Copy"** button
3. Files download and will now auto-sync

### Remove Local Copy  
1. Select files with local copies
2. Click **"Remove Local Copy"** button
3. Local files deleted, but remain on OneDrive
4. Files stop syncing until you "Keep Local Copy" again

### Viewing Logs
Logs are automatically viewable in the GUI:
- Click **View → Logs** to see real-time sync activity

Or view from terminal:
```bash
tail -f ~/.config/odsc/odsc.log
```

## Troubleshooting

### Application not in menu after installation?
Try one of these:
```bash
# Option 1: Search for it
# Press Super key, type "OneDrive"

# Option 2: Refresh desktop entries
./refresh-desktop-entry.sh

# Option 3: Launch from terminal
odsc-gui
```

### Sync not working?
```bash
# Check if daemon is running
systemctl --user status odsc

# View daemon logs
journalctl --user -u odsc -f

# Restart daemon
systemctl --user restart odsc
```

### Local state corrupted or out of sync? (Advanced)
⚠️ **WARNING: This will delete all local files and re-download from OneDrive!**

```bash
# See what would be deleted (safe)
odsc-reset-local --dry-run

# Actually reset local state (requires --force)
odsc-reset-local --force

# Reset without auto-restarting daemon
odsc-reset-local --force --no-restart
```

This utility:
- Stops the daemon
- Deletes ALL local files and folders
- Clears sync state and cache
- Keeps authentication token
- Re-syncs everything from OneDrive (treated as authoritative)

Use this when local state becomes corrupted or you want a fresh start.

### Authentication failed?
1. Ensure port 8080 is available
2. Check internet connection
3. Try **Authentication → Login** again

### Need to reinstall?
```bash
# Uninstall
pip3 uninstall odsc -y

# Reinstall
./install.sh
```

## Command Line Tools

```bash
odsc-gui            # Launch GUI application
odsc-daemon         # Run sync daemon (if not using systemd)
odsc auth           # Authenticate from command line
odsc status         # View sync status
odsc-reset-local    # Reset local state (ADVANCED - see Troubleshooting)
```

## Systemd Service Commands

```bash
# Check status
systemctl --user status odsc

# Start/stop
systemctl --user start odsc
systemctl --user stop odsc

# Restart (after config changes)
systemctl --user restart odsc

# Enable/disable auto-start
systemctl --user enable odsc
systemctl --user disable odsc

# View logs
journalctl --user -u odsc -f
```

## File Locations

```
~/.config/odsc/
├── config.json           # Configuration file
├── sync_state.json       # Sync state and cache
├── .onedrive_token       # Encrypted authentication token
└── odsc.log              # Application logs

~/OneDrive/               # Default sync directory (configurable)

~/.local/bin/             # Installed executables
├── odsc-gui
├── odsc-daemon
├── odsc
└── ...
```

## Uninstallation

```bash
# Stop and disable service
systemctl --user stop odsc
systemctl --user disable odsc

# Uninstall package
pip3 uninstall odsc -y

# Remove configuration (optional)
rm -rf ~/.config/odsc

# Remove desktop entry (optional)
rm ~/.local/share/applications/com.github.odsc.desktop

# Remove sync directory (optional - contains your files!)
# rm -rf ~/OneDrive
```

## Security

- ✅ OAuth2 authentication with CSRF protection
- ✅ Encrypted token storage using system keyring
- ✅ SSL/TLS certificate validation
- ✅ Path traversal protection
- ✅ No sensitive data in logs

## License

MIT License - See [LICENSE](LICENSE) file for details.

## Contributing

Contributions welcome! Please submit a Pull Request on GitHub.

## Support

- **Issues:** Report bugs on the [GitHub Issues](https://github.com/marlobello/odsc/issues) page
- **Logs:** Check `~/.config/odsc/odsc.log` for debugging information

