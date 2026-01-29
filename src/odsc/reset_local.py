#!/usr/bin/env python3
"""Reset local sync state - for advanced users only.

WARNING: This is a destructive operation!

This utility will:
1. Stop the ODSC daemon (if running)
2. Delete ALL files and folders in the sync directory
3. Clear the sync state file (cache, delta token, file states)
4. Keep authentication token intact
5. Optionally restart the daemon to re-sync from OneDrive

Use this when local state becomes corrupted or out of sync with OneDrive.
OneDrive is treated as the authoritative source.
"""

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

# Add src directory to path for imports
src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from odsc.config import Config


logger = logging.getLogger(__name__)


def check_daemon_running() -> bool:
    """Check if ODSC daemon is running.
    
    Returns:
        True if daemon is running, False otherwise
    """
    try:
        result = subprocess.run(
            ['systemctl', '--user', 'is-active', 'odsc'],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def stop_daemon() -> bool:
    """Stop the ODSC daemon.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        subprocess.run(
            ['systemctl', '--user', 'stop', 'odsc'],
            check=True,
            capture_output=True
        )
        print("✓ Daemon stopped")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to stop daemon: {e}")
        return False


def start_daemon() -> bool:
    """Start the ODSC daemon.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        subprocess.run(
            ['systemctl', '--user', 'start', 'odsc'],
            check=True,
            capture_output=True
        )
        print("✓ Daemon started")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to start daemon: {e}")
        return False


def delete_sync_directory(sync_dir: Path, dry_run: bool = False) -> tuple[int, int]:
    """Delete all contents of sync directory.
    
    Args:
        sync_dir: Path to sync directory
        dry_run: If True, only show what would be deleted
        
    Returns:
        Tuple of (file_count, folder_count)
    """
    file_count = 0
    folder_count = 0
    
    if not sync_dir.exists():
        print(f"  Sync directory doesn't exist: {sync_dir}")
        return 0, 0
    
    # Count items first
    for item in sync_dir.rglob('*'):
        if item.is_file():
            file_count += 1
        elif item.is_dir():
            folder_count += 1
    
    if dry_run:
        print(f"  Would delete: {file_count} files, {folder_count} folders")
        return file_count, folder_count
    
    # Actually delete
    for item in sync_dir.iterdir():
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)
    
    print(f"  ✓ Deleted: {file_count} files, {folder_count} folders")
    return file_count, folder_count


def clear_sync_state(state_path: Path, dry_run: bool = False) -> bool:
    """Clear sync state file (keep auth token).
    
    Args:
        state_path: Path to sync_state.json
        dry_run: If True, only show what would be cleared
        
    Returns:
        True if successful, False otherwise
    """
    if not state_path.exists():
        print(f"  State file doesn't exist: {state_path}")
        return True
    
    if dry_run:
        print(f"  Would clear state file: {state_path}")
        return True
    
    # Create minimal empty state
    import json
    empty_state = {
        'delta_token': None,
        'file_cache': {},
        'files': {},
        'last_sync': None
    }
    
    with open(state_path, 'w') as f:
        json.dump(empty_state, f, indent=2)
    
    state_path.chmod(0o600)
    print(f"  ✓ Cleared state file: {state_path}")
    return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Reset local ODSC sync state (ADVANCED USERS ONLY)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
⚠️  WARNING: This is a DESTRUCTIVE operation! ⚠️

This utility will:
  1. Stop the ODSC daemon (if running)
  2. Delete ALL files and folders in your sync directory
  3. Clear the sync state file (cache, delta token, file states)
  4. Keep your authentication token intact
  5. Optionally restart the daemon to re-sync from OneDrive

OneDrive will be treated as the authoritative source.
All files will be re-downloaded from OneDrive.

Use this when local state becomes corrupted or out of sync.
        """
    )
    
    parser.add_argument(
        '--force',
        action='store_true',
        help='Required to confirm destructive operation'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be deleted without actually deleting'
    )
    
    parser.add_argument(
        '--no-restart',
        action='store_true',
        help='Do not restart daemon after reset'
    )
    
    args = parser.parse_args()
    
    # Require --force for actual operation
    if not args.force and not args.dry_run:
        print("ERROR: This is a destructive operation!")
        print("Use --dry-run to see what would be deleted, or")
        print("Use --force to confirm you want to proceed.")
        return 1
    
    # Load config
    try:
        config = Config()
    except Exception as e:
        print(f"ERROR: Failed to load config: {e}")
        return 1
    
    sync_dir = config.sync_directory
    state_path = config.state_path
    
    # Show operation summary
    if args.dry_run:
        print("=== DRY RUN MODE (no changes will be made) ===\n")
    else:
        print("=== RESETTING LOCAL SYNC STATE ===\n")
    
    print(f"Sync directory: {sync_dir}")
    print(f"State file: {state_path}")
    print()
    
    # Check if daemon is running
    daemon_was_running = check_daemon_running()
    
    if daemon_was_running:
        print("1. Stopping daemon...")
        if not args.dry_run:
            if not stop_daemon():
                print("ERROR: Failed to stop daemon. Aborting.")
                return 1
        else:
            print("  Would stop daemon")
    else:
        print("1. Daemon is not running")
    
    print()
    
    # Delete sync directory contents
    print("2. Deleting sync directory contents...")
    file_count, folder_count = delete_sync_directory(sync_dir, args.dry_run)
    print()
    
    # Clear sync state
    print("3. Clearing sync state...")
    if not clear_sync_state(state_path, args.dry_run):
        print("ERROR: Failed to clear state file")
        return 1
    print()
    
    # Restart daemon
    if not args.no_restart and daemon_was_running:
        print("4. Restarting daemon...")
        if not args.dry_run:
            if start_daemon():
                print()
                print("✓ Reset complete! Daemon will re-sync from OneDrive.")
                print("  Monitor logs: journalctl --user -u odsc -f")
            else:
                print()
                print("⚠ Reset complete but daemon failed to start.")
                print("  Start manually: systemctl --user start odsc")
        else:
            print("  Would restart daemon")
    else:
        print("4. Skipping daemon restart")
        if not args.dry_run:
            print()
            print("✓ Reset complete!")
            if daemon_was_running:
                print("  Start daemon: systemctl --user start odsc")
    
    print()
    
    if args.dry_run:
        print("=== DRY RUN COMPLETE (no changes made) ===")
        print("Remove --dry-run and add --force to perform reset")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
