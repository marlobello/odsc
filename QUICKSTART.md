# Quick Start Guide

## Installation

```bash
# Clone the repository
git clone https://github.com/marlobello/odsc.git
cd odsc

# Run the installation script
./install.sh
```

## First Use

```bash
# Start the GUI
odsc-gui
```

1. Click **Authenticate** button
2. Browser will open for Microsoft login
3. Log in with your Microsoft account
4. Authorize the application
5. Return to the GUI - authentication completes automatically

**Note**: ODSC uses a built-in Azure app registration. No manual setup required!

## Configure Sync

1. Click **Settings** to configure:
   - **Sync Directory**: Where files are stored locally (default: `~/OneDrive`)
   - **Sync Interval**: How often to check for changes (default: 300 seconds)

## Start Background Sync

### Option 1: Manual

```bash
odsc-daemon
```

### Option 2: Systemd Service (Auto-start)

If you used `./install.sh`, the systemd service should already be installed. Otherwise:

```bash
# Install service file (if not done by install.sh)
mkdir -p ~/.config/systemd/user/
cp systemd/odsc.service ~/.config/systemd/user/

# Enable and start service
systemctl --user enable odsc
systemctl --user start odsc

# Check status
systemctl --user status odsc

# View logs
journalctl --user -u odsc -f
```

## Using the GUI

### View OneDrive Files
- The main window shows all files in your OneDrive
- **Local** checkbox indicates if file has a local copy

### Keep Local Copy (Download)
1. Select files that aren't downloaded (Local checkbox is unchecked)
2. Click **Keep Local Copy**
3. Files are downloaded to your sync directory
4. Files will now be automatically kept in sync

### Remove Local Copy
1. Select files that are downloaded (Local checkbox is checked)
2. Click **Remove Local Copy**
3. Local copy deleted, but file remains on OneDrive
4. File stops being automatically synced
5. Can be re-downloaded anytime

### Upload Files
- Simply copy files to your sync directory (e.g., `~/OneDrive`)
- Files are automatically uploaded in the background

### Refresh
- Click **Refresh** to update the file list from OneDrive

## Tips

- Files are **uploaded automatically** when added to sync directory
- Files are **downloaded manually** via "Keep Local Copy" button (selective sync)
- Once downloaded, files are **automatically kept in sync** with OneDrive
- Local deletions do **NOT** delete from OneDrive (safety feature)
- Remote deletions move local files to **system trash** (recoverable)
- Conflicts create **`.conflict`** files (both versions preserved)
- Monitor sync status via: `systemctl --user status odsc`
- View logs: `journalctl --user -u odsc -f` or `~/.config/odsc/odsc.log`

## Troubleshooting

### "Not authenticated" error
- Ensure you've completed the authentication flow
- Check token file exists: `ls ~/.config/odsc/.onedrive_token`

### Sync not working
- Check daemon is running: `systemctl --user status odsc`
- View logs: `journalctl --user -u odsc -f`

### GUI won't start
- Install GTK: `sudo apt-get install gir1.2-gtk-3.0 python3-gi`
- Check error: Run `odsc-gui` from terminal

## Advanced Usage

### Manual Token Refresh
If authentication expires, re-run the authentication flow in the GUI.

### Custom Configuration
Edit `~/.config/odsc/config.json` directly:
```json
{
  "sync_directory": "/home/user/OneDrive",
  "sync_interval": 300,
  "client_id": "",
  "auto_start": false,
  "log_level": "INFO"
}
}
```

### Multiple Sync Directories
Currently, only one sync directory is supported per configuration.

## Next Steps

- Set up automatic sync: `systemctl --user enable odsc`
- Add files to `~/OneDrive` to start syncing
- Use GUI to selectively download files you need locally

For detailed documentation, see [README.md](README.md)
