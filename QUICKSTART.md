# Quick Start Guide

## Installation

```bash
# Clone the repository
git clone https://github.com/marlobello/odsc.git
cd odsc

# Run the installation script
./install.sh
```

## Azure App Registration

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to **Azure Active Directory** → **App registrations** → **New registration**
3. Settings:
   - **Name**: OneDrive Sync Client (or any name)
   - **Supported account types**: "Accounts in any organizational directory and personal Microsoft accounts"
   - **Redirect URI**: Web → `http://localhost:8080`
4. Click **Register**
5. Copy the **Application (client) ID**
6. Go to **API permissions** → **Add permission** → **Microsoft Graph** → **Delegated permissions**
7. Add: `Files.ReadWrite` and `offline_access`
8. Click **Grant admin consent for [Your Directory]**

## First Use

```bash
# Start the GUI
odsc-gui
```

1. Click **Authenticate** button
2. Enter your **Azure Application Client ID**
3. Browser will open for Microsoft login
4. Log in and authorize the application
5. Return to the GUI - authentication completes automatically

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

```bash
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
- **Local** checkbox indicates if file is downloaded

### Download Files
1. Select files that aren't downloaded (Local checkbox is unchecked)
2. Click **Download Selected**
3. Files are downloaded to your sync directory

### Upload Files
- Simply copy files to your sync directory (e.g., `~/OneDrive`)
- Files are automatically uploaded in the background

### Refresh
- Click **Refresh** to update the file list from OneDrive

## Tips

- Files are **uploaded automatically** when added to sync directory
- Files are **downloaded manually** via the GUI (selective sync)
- Local deletions do **NOT** delete from OneDrive (safety feature)
- Monitor sync status via: `systemctl --user status odsc`

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
  "client_id": "your-client-id"
}
```

### Multiple Sync Directories
Currently, only one sync directory is supported per configuration.

## Next Steps

- Set up automatic sync: `systemctl --user enable odsc`
- Add files to `~/OneDrive` to start syncing
- Use GUI to selectively download files you need locally

For detailed documentation, see [README.md](README.md)
