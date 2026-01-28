# OneDrive Consumer Support - No Azure Registration Required

## Overview

ODSC now works directly with OneDrive Consumer accounts (including Microsoft 365 Family subscriptions) without requiring users to register an Azure application.

## What Changed

### Default Client ID

ODSC now uses Microsoft's legacy OneDrive application client ID (`0000000040126752`) as the default. This is a well-known public client that:
- Works with OneDrive personal accounts
- Doesn't require app registration
- Is commonly used by OneDrive sync clients
- Supports the Microsoft Graph API for file operations

### Simplified Authentication Flow

**Before:**
1. Go to Azure Portal
2. Create app registration
3. Configure permissions
4. Get client ID
5. Enter client ID in ODSC
6. Authenticate with Microsoft

**After:**
1. Click "Authenticate" in GUI or run `odsc auth`
2. Log in with Microsoft account
3. Done!

### Key Features

- **No Azure Portal Required**: Users don't need an Azure account or portal access
- **One-Click Authentication**: GUI authentication is now a single button click
- **Command-Line Simplicity**: `odsc auth` works without any parameters
- **Backward Compatible**: Custom client IDs can still be used if needed

## Usage

### GUI Authentication

```bash
odsc-gui
```

1. Click "Authenticate" button
2. Browser opens to Microsoft login
3. Log in with your Microsoft account
4. Authorize the application
5. Return to GUI - you're authenticated!

### CLI Authentication

```bash
odsc auth
```

That's it! The command opens your browser, you log in, and authentication is complete.

### Custom Client ID (Optional)

If you have your own Azure app and want to use a custom client ID:

**Via CLI:**
```bash
odsc auth --client-id YOUR_CLIENT_ID
```

**Via Config:**
Edit `~/.config/odsc/config.json`:
```json
{
  "sync_directory": "/home/user/OneDrive",
  "sync_interval": 300,
  "client_id": "YOUR_CLIENT_ID"
}
```

## Technical Details

### Client ID Used

- **Default**: `0000000040126752` (Microsoft's legacy OneDrive client)
- **Type**: Public client (no client secret required)
- **Permissions**: Files.ReadWrite, offline_access
- **Scope**: OneDrive Consumer (personal accounts)

### Implementation Changes

1. **OneDriveClient** (`src/odsc/onedrive_client.py`):
   - Added `DEFAULT_CLIENT_ID` constant
   - Made `client_id` parameter optional in `__init__`
   - Uses default client ID if none provided

2. **Config** (`src/odsc/config.py`):
   - Removed `client_id` from default configuration
   - `client_id` property returns empty string if not set
   - Maintains backward compatibility

3. **Daemon** (`src/odsc/daemon.py`):
   - No longer requires client_id to be configured
   - Uses default client ID if none provided

4. **GUI** (`src/odsc/gui.py`):
   - Removed AuthDialog that asked for client ID
   - Authentication is now direct from toolbar button
   - Simplified authentication flow

5. **CLI** (`src/odsc/cli.py`):
   - `auth` command no longer requires --client-id
   - `list` and other commands work without client ID
   - `status` shows "(using default)" when no custom client ID

### Security Considerations

- The default client ID is a **public client** identifier
- It's safe to include in source code and documentation
- It's widely used by other OneDrive sync implementations
- OAuth2 flow still requires user authentication and consent
- Tokens are stored securely with 0600 permissions

### Compatibility

This change is **fully backward compatible**:
- Existing configs with custom client IDs continue to work
- Users can still specify custom client IDs via CLI or config
- No breaking changes to API or functionality

## Benefits

1. **Easier Setup**: No Azure Portal registration required
2. **Better UX**: Fewer steps to get started
3. **Works with OneDrive Consumer**: Perfect for Microsoft 365 Family users
4. **Still Secure**: Full OAuth2 authentication maintained
5. **Flexible**: Custom client IDs still supported for advanced users

## Testing

All existing tests pass:
- Configuration tests ✓
- Python syntax validation ✓
- CLI commands work without client ID ✓
- GUI authentication simplified ✓

## Documentation Updates

- **README.md**: Removed Azure app registration section
- **QUICKSTART.md**: Simplified to remove Azure setup steps
- **config.example.json**: Removed client_id field
- All references to Azure Portal removed or marked optional

## For Advanced Users

If you prefer to use your own Azure app registration:
1. Register app at https://portal.azure.com
2. Set redirect URI to `http://localhost:8080`
3. Add permissions: Files.ReadWrite, offline_access
4. Use `odsc auth --client-id YOUR_ID` or set in config.json

## Conclusion

ODSC is now significantly easier to use for OneDrive Consumer users. No Azure knowledge or app registration required - just authenticate with your Microsoft account and start syncing!
