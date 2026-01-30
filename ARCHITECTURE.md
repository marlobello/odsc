# Architecture Documentation

## Overview

OneDrive Sync Client (ODSC) is designed as a modular, event-driven sync client for Ubuntu/GNOME systems. It consists of two main components: a background daemon and a GTK-based GUI.

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         User Layer                           │
├─────────────────────────────────────────────────────────────┤
│  GNOME GUI (odsc-gui)          │  File System (~/OneDrive)  │
│  - File browser                │  - Local sync directory    │
│  - Authentication UI           │  - User files              │
│  - Settings                    │                            │
└──────────────┬─────────────────┴──────────────┬─────────────┘
               │                                 │
               │ API calls                       │ File events
               │                                 │
┌──────────────▼─────────────────────────────────▼─────────────┐
│                      Core Services                            │
├───────────────────────────────────────────────────────────────┤
│  Sync Daemon (odsc-daemon)                                   │
│  ┌─────────────────┐  ┌──────────────────┐                  │
│  │ File Watcher    │  │ Sync Engine      │                  │
│  │ (Watchdog)      │─▶│ - Upload logic   │                  │
│  │                 │  │ - State tracking │                  │
│  └─────────────────┘  └──────────────────┘                  │
│                              │                                │
│  ┌─────────────────┐         │                               │
│  │ OneDrive Client │◀────────┘                              │
│  │ - OAuth2        │                                         │
│  │ - Graph API     │                                         │
│  └────────┬────────┘                                         │
└───────────┼──────────────────────────────────────────────────┘
            │ HTTPS
            │
┌───────────▼──────────────────────────────────────────────────┐
│            Microsoft Graph API / OneDrive                     │
└───────────────────────────────────────────────────────────────┘
```

## Components

### 1. Configuration Manager (`config.py`)

**Responsibility**: Manages application configuration and persistent data.

**Key Features**:
- Stores user preferences (sync directory, interval, client ID)
- Manages OAuth tokens securely
- Tracks sync state (file metadata, last sync time)
- Uses JSON files in `~/.config/odsc/`

**Files**:
- `config.json`: User settings (sync_directory, sync_interval, log_level, client_id)
- `.onedrive_token`: Encrypted OAuth credentials (uses keyring + cryptography)
- `sync_state.json`: Sync metadata (file mtime, size, eTag, downloaded status)
- `odsc.log`: Application logs

### 2. OneDrive Client (`onedrive_client.py`)

**Responsibility**: Interface with Microsoft OneDrive via Graph API.

**Key Features**:
- OAuth2 authentication flow
- Token refresh management
- File operations (list, upload, download, delete)
- Recursive directory traversal
- Handles API rate limiting and errors

**API Endpoints Used**:
- Authentication: `login.microsoftonline.com/oauth2/v2.0`
- Files: `graph.microsoft.com/v1.0/me/drive`

### 3. Sync Daemon (`daemon.py`)

**Responsibility**: Background service for automatic file synchronization.

**Key Features**:
- **Event-driven sync**: Monitors file system changes using Watchdog
- **Periodic two-way sync**: Full scan at configurable intervals (default: 5 minutes)
- **Automatic uploads**: New/modified local files uploaded to OneDrive
- **Selective downloads**: Only syncs files user has marked as "downloaded"
- **State tracking**: Maintains file metadata to detect changes
- **Thread-safe**: Uses locks for concurrent access
- **Conflict detection**: Identifies simultaneous local and remote changes

**Sync Logic**:
1. **File Event**: Watchdog detects local change → Queue for sync
2. **Periodic Check**: Every N seconds, scan all local and remote files
3. **Change Detection**: Compare local mtime, remote eTag against stored state
4. **Sync Decision**: Determine action (upload, download, conflict, skip)
5. **Execute**: Perform upload/download/conflict resolution
6. **Update State**: Record new metadata for synced files

**Design Decisions**:
- **Selective sync**: Remote files not auto-downloaded (user must opt-in via GUI)
- **Safe deletions**: Local deletions don't affect OneDrive, remote deletions move to trash
- **Conflict preservation**: Both versions kept when simultaneous edits occur
- **Debouncing**: Batch rapid changes before uploading

### 4. GNOME GUI (`gui.py`)

**Responsibility**: User interface for viewing and managing OneDrive files.

**Key Features**:
- **Authentication**: OAuth flow with local callback server (port 8080)
- **File Browser**: TreeView showing all OneDrive files with status
- **"Keep Local Copy"**: Download files/folders and enable automatic sync
- **"Remove Local Copy"**: Delete local file/folder, keep on OneDrive, disable sync
- **Folder Support**: Both "Keep Local Copy" and "Remove Local Copy" work recursively on folders
- **Status Indicators**: Checkbox shows which files have local copies
- **Settings**: Configure sync directory, interval, and logging
- **Refresh**: Manual update of file list from OneDrive

**UI Components**:
- Main window with toolbar (Authenticate, Settings, Refresh)
- File list (TreeView with columns: Name, Size, Modified, Local Status)
- Action buttons: "Keep Local Copy", "Remove Local Copy"
- Settings dialog (sync directory, sync interval, log level)
- Status bar

**Threading Model**:
- UI runs on main thread (GTK requirement)
- API calls run on background threads
- Use `GLib.idle_add()` for thread-safe UI updates

## Data Flow

### Upload Flow (Daemon)

```
Local File Change
    ↓
Watchdog Event
    ↓
Queue Change
    ↓
Sync Thread Picks Up
    ↓
Check if File Modified (compare mtime)
    ↓
Upload to OneDrive API
    ↓
Update Sync State
```

### Download Flow (GUI)

```
User Selects File
    ↓
Get File Metadata
    ↓
Determine Local Path
    ↓
Download from OneDrive API
    ↓
Save to Sync Directory
    ↓
Refresh File List
```

### Authentication Flow

```
User Clicks "Authenticate"
    ↓
Open Browser to Microsoft (with default client ID)
    ↓
User Logs In
    ↓
Redirect to localhost:8080
    ↓
Receive Authorization Code
    ↓
Exchange for Access Token
    ↓
Store Token + Refresh Token
```

## File Sync Strategy

### What Gets Synced

- **All files** in the sync directory (default: `~/OneDrive`)
- **Recursive**: All subdirectories
- **Immediate**: Changes detected within seconds (Watchdog)
- **Periodic**: Full check every 5 minutes (configurable)

### What Doesn't Get Synced

- Hidden files (starting with `.`)
- System files
- Temporary files

### Conflict Resolution

**Implemented** - When both local and remote versions of a file are modified:
- Both versions are preserved
- Local version remains unchanged at original path
- Remote version downloaded as `filename.conflict`
- User manually resolves by reviewing both versions
- System makes no assumptions about which version is correct

## Security Considerations

### Token Storage
- Tokens stored in user's home directory
- File permissions: 600 (user read/write only)
- Refresh tokens allow long-term access
- No password storage

### API Security
- All communication over HTTPS
- OAuth2 standard authentication
- Scopes limited to Files.ReadWrite
- No server-side storage of credentials

### Default Client ID
- Uses Microsoft's public client ID for OneDrive authentication
- Safe to include in source code (public client identifier)
- Widely used by OneDrive sync implementations

### Recommendations
- Default authentication works for most users (OneDrive Consumer)
- Custom client IDs can be configured for advanced scenarios
- Regularly review app permissions in Microsoft account

## Performance Considerations

### File Watching
- Uses OS-level file system events (inotify on Linux)
- Low CPU overhead when idle
- Efficient directory monitoring

### API Rate Limiting
- Microsoft Graph has rate limits
- Client implements exponential backoff (TODO)
- Batch operations where possible

### Large Files
- Streaming upload/download
- Chunk size: 8KB for downloads
- No size limit (handled by API)

## Extensibility

### Adding Features

**Two-way Sync**:
1. Modify daemon to periodically check OneDrive for changes
2. Download changed files
3. Implement conflict detection

**File Filters**:
1. Add ignore patterns to config
2. Check patterns in sync event handler
3. Add UI for managing filters

**Progress Indicators**:
1. Add progress tracking to OneDrive client
2. Emit progress events
3. Update GUI with progress bars

### Plugin Architecture

Future consideration: Plugin system for:
- Custom sync strategies
- File transformations
- Multiple cloud providers

## Testing Strategy

### Unit Tests
- Configuration management
- OneDrive API client (with mocks)
- Sync logic

### Integration Tests
- Full authentication flow
- File upload/download
- Daemon operation

### Manual Testing
- GUI functionality
- Systemd service
- Error handling

## Deployment

### Installation Methods
1. **Development**: `pip install -e .`
2. **User**: `./install.sh`
3. **System**: Package as .deb (future)

### Dependencies
- **Runtime**: Python 3.8+, GTK 3.0, D-Bus
- **Python packages**: See requirements.txt
- **System**: Linux with systemd (optional)

## Future Enhancements

1. **Bandwidth Control**: Limit upload/download speed
2. **File Exclusions**: Ignore patterns (.gitignore style)
3. **System Tray**: Minimize to tray with status icon
4. **Notifications**: Desktop notifications for sync events
5. **Multi-account**: Support multiple OneDrive accounts
6. **Sharing**: Manage OneDrive sharing from GUI
7. **Offline Mode**: Queue operations when offline
8. **Delta Sync**: Already implemented! Uses OneDrive delta API for efficiency
9. **Progress Bars**: Real-time upload/download progress in GUI

## Known Limitations

1. **Selective Sync**: New remote files require manual download ("Keep Local Copy")
2. **Single Directory**: One sync folder per config
3. **No Local Encryption**: Files not encrypted at rest locally (relies on OneDrive encryption)
4. **Linux Only**: Designed for Ubuntu/GNOME (requires GTK 3.0)
5. **No Versioning UI**: Can't access OneDrive file version history from GUI
6. **No Bandwidth Throttling**: Uploads/downloads use full available bandwidth

## Troubleshooting

### Common Issues

**Import Errors**:
- Solution: Install GTK bindings with system package manager

**Authentication Fails**:
- Check redirect URI is exactly `http://localhost:8080`
- Verify permissions granted during Microsoft login

**Files Not Syncing**:
- Check daemon is running
- Verify file is in sync directory
- Check logs for errors

**High CPU Usage**:
- Watchdog monitoring many files
- Reduce sync interval
- Exclude large directories

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:
- Code style
- Pull request process
- Feature proposals
- Bug reporting

## References

- [Microsoft Graph API](https://docs.microsoft.com/en-us/graph/)
- [OneDrive API](https://docs.microsoft.com/en-us/onedrive/developer/)
- [Watchdog Documentation](https://python-watchdog.readthedocs.io/)
- [PyGObject Documentation](https://pygobject.readthedocs.io/)
