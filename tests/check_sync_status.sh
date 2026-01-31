#!/bin/bash
# Quick sync status checker

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║              ODSC SYNC STATUS CHECK                            ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Check daemon status
echo "Daemon Status:"
systemctl --user status odsc --no-pager | head -5
echo ""

# Check recent logs
echo "Recent Logs (last 50 lines):"
echo "─────────────────────────────────────────────────────────────────"
journalctl --user -u odsc -n 50 --no-pager | grep -E "(deleted|Creating|Skipping|upload|download|Folder|ERROR|WARNING)" | tail -30
echo "─────────────────────────────────────────────────────────────────"
echo ""

# Check sync state
echo "Sync State Summary:"
python3 << 'PYTHON'
import json
from pathlib import Path

state_file = Path.home() / ".config" / "odsc" / "sync_state.json"
if state_file.exists():
    with open(state_file) as f:
        state = json.load(f)
    
    files_count = len(state.get('files', {}))
    cache_count = len(state.get('file_cache', {}))
    last_sync = state.get('last_sync', 'Never')
    
    print(f"  Files tracked: {files_count}")
    print(f"  Items in cache: {cache_count}")
    print(f"  Last sync: {last_sync}")
    
    # Count folders
    folders = sum(1 for v in state.get('file_cache', {}).values() if 'folder' in v or v.get('is_folder'))
    print(f"  Folders in cache: {folders}")
else:
    print("  No state file found")
PYTHON

echo ""
echo "To see full logs: journalctl --user -u odsc -f"
