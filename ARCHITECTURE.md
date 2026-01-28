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
- `config.json`: User settings
- `.onedrive_token`: OAuth credentials (sensitive)
- `sync_state.json`: Sync metadata

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
- **Periodic sync**: Full scan at configurable intervals
- **Upload-only**: Automatically uploads new/modified files
- **State tracking**: Maintains file modification times to avoid re-uploads
- **Thread-safe**: Uses locks for concurrent access

**Sync Logic**:
1. **File Event**: Watchdog detects change → Queue for sync
2. **Periodic Check**: Every N seconds, scan all files
3. **Upload Decision**: Compare local mtime with stored mtime
4. **Upload**: Send to OneDrive via API
5. **Update State**: Record new mtime

**Design Decisions**:
- **Upload-only by default**: Prevents accidental data loss
- **No auto-delete**: Local deletions don't affect OneDrive
- **Debouncing**: Batch rapid changes before uploading

### 4. GNOME GUI (`gui.py`)

**Responsibility**: User interface for viewing and managing OneDrive files.

**Key Features**:
- **Authentication**: OAuth flow with local callback server
- **File Browser**: TreeView showing all OneDrive files
- **Selective Download**: User-initiated downloads only
- **Status Indicators**: Shows which files are local
- **Settings**: Configure sync directory and interval

**UI Components**:
- Main window with toolbar
- File list (TreeView)
- Authentication dialog
- Settings dialog
- Status bar

**Threading Model**:
- UI runs on main thread
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
Enter Client ID
    ↓
Open Browser to Microsoft
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

Currently **not implemented**. Plans for future:
- Last-write-wins strategy
- Optional conflict files (.conflict)
- User notification for conflicts

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

### Recommendations
- Register app in personal Azure tenant
- Don't share Client ID publicly for production use
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

1. **Conflict Resolution**: Detect and handle file conflicts
2. **Two-way Sync**: Download changes from OneDrive
3. **Bandwidth Control**: Limit upload/download speed
4. **File Exclusions**: Ignore patterns (.gitignore style)
5. **System Tray**: Minimize to tray with status icon
6. **Notifications**: Desktop notifications for sync events
7. **Multi-account**: Support multiple OneDrive accounts
8. **Sharing**: Manage OneDrive sharing from GUI
9. **Offline Mode**: Queue operations when offline
10. **Delta Sync**: Use OneDrive delta API for efficiency

## Known Limitations

1. **Upload-only**: No automatic downloads
2. **No Conflict Detection**: Last write wins
3. **Single Directory**: One sync folder per config
4. **No Encryption**: Files not encrypted at rest locally
5. **Linux Only**: Designed for Ubuntu/GNOME
6. **No Versioning UI**: Can't access OneDrive file versions

## Troubleshooting

### Common Issues

**Import Errors**:
- Solution: Install GTK bindings with system package manager

**Authentication Fails**:
- Check redirect URI is exactly `http://localhost:8080`
- Verify permissions granted in Azure

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
