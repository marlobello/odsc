# ODSC Comprehensive Testing Instructions

## 🔴 CRITICAL DISCOVERY

Your daemon was running **OLD CODE** from before the fixes were deployed!

- **Daemon started**: Fri 2026-01-30 13:00:43 CST
- **Code last updated**: 2026-01-30 10:59:14 (before daemon start)
- **Result**: All our fixes were NOT active in the running daemon

**The daemon has now been RESTARTED with the new code at 13:21:00**

---

## Testing Tools Provided

### 1. **Comprehensive Test Suite** (`test_sync_scenarios.py`)
Interactive test that guides you through all sync scenarios:
- ✓ Files created in OneDrive
- ✓ Files created locally
- ✓ Files deleted from OneDrive
- ✓ Files deleted locally
- ✓ Files updated in OneDrive
- ✓ Files updated locally
- ✓ Folders created in OneDrive
- ✓ Folders created locally
- ✓ Folders deleted from OneDrive ⭐ (THE BUG)
- ✓ Folders deleted locally

**Usage:**
```bash
cd /home/marlo/git/odsc
python3 test_sync_scenarios.py
```

### 2. **Quick Status Checker** (`check_sync_status.sh`)
Shows daemon status, recent logs, and sync state summary.

**Usage:**
```bash
./check_sync_status.sh
```

### 3. **Deletion Diagnostic** (`diagnose_deletion.sh`)
Specifically checks if deletion fixes are deployed and working.

**Usage:**
```bash
./diagnose_deletion.sh
```

---

## Quick Deletion Test

To quickly test if the folder deletion bug is fixed:

### Step 1: Create a test folder in OneDrive
1. Go to OneDrive web interface
2. Create a new folder: `TestDeletionFix`
3. Wait 5 minutes for sync (or force: `touch ~/.config/odsc/.force_sync`)
4. Verify folder appears locally: `ls ~/OneDrive/TestDeletionFix`

### Step 2: Delete the folder from OneDrive
1. Go to OneDrive web interface
2. Delete `TestDeletionFix` folder
3. Watch logs in real-time:
```bash
journalctl --user -u odsc -f | grep -E "(delet|TestDeletionFix)"
```

### Step 3: Verify correct behavior
After 5 minutes (or force sync):

**Expected logs to see:**
```
Folder deleted on OneDrive: TestDeletionFix
Added TestDeletionFix to deleted tracking set
Moved folder to recycle bin: TestDeletionFix
Deletion verified successful: TestDeletionFix
```
(or "Deletion incomplete, retrying" if there were issues)

**Expected local behavior:**
- `ls ~/OneDrive/TestDeletionFix` → should not exist
- Check trash: Folder should be in recycle bin

**Expected OneDrive behavior:**
- Folder should STAY deleted (not re-appear)

### Step 4: Next sync cycle
Wait another 5 minutes:
- Folder should STILL be gone locally
- Folder should STILL be gone from OneDrive
- No "Creating folder" messages in logs

---

## Log Patterns to Watch For

### ✅ GOOD (Fixed Code Working):
```
Folder deleted on OneDrive: [path]
Added [path] to deleted tracking set
Moved folder to recycle bin: [path]
Deletion verified successful: [path]
```
or if deletion struggled:
```
Deletion incomplete, retrying: [path]
Directory deleted on retry 2: [path]
```

### ❌ BAD (Bug Still Present):
```
Creating folder on OneDrive: [path that was just deleted]
```

### 🤔 NEEDS INVESTIGATION:
```
Could not delete after 3 attempts: [path]
Skipping upload of [path] - was deleted from OneDrive in this sync
```
(This means deletion failed but upload blocked - folder orphaned locally)

---

## Viewing Logs

### Live logs (follow mode):
```bash
journalctl --user -u odsc -f
```

### Filtered for deletions:
```bash
journalctl --user -u odsc -f | grep -i delet
```

### Recent logs (last 100 lines):
```bash
journalctl --user -u odsc -n 100 --no-pager
```

### Logs since a specific time:
```bash
journalctl --user -u odsc --since "10 minutes ago"
```

---

## Force a Sync

If you want to trigger a sync immediately instead of waiting 5 minutes:

```bash
touch ~/.config/odsc/.force_sync
```

Then watch the logs to see sync start.

---

## Expected Behavior Summary

### ✅ Files
- **Created in OneDrive**: Appear in GUI, not auto-downloaded (selective sync)
- **Created locally**: Auto-uploaded to OneDrive
- **Deleted from OneDrive**: Moved to local trash (OneDrive authoritative)
- **Deleted locally**: Stay on OneDrive (local deletions don't propagate)
- **Updated in OneDrive**: Downloaded locally (if marked for sync)
- **Updated locally**: Uploaded to OneDrive

### ✅ Folders
- **Created in OneDrive**: Created locally automatically
- **Created locally**: Uploaded to OneDrive automatically
- **Deleted from OneDrive**: Deleted locally (OneDrive authoritative) ⭐
- **Deleted locally**: Stay on OneDrive (local deletions don't propagate)

---

## If Problems Persist

1. Check daemon is running latest code:
   ```bash
   ./diagnose_deletion.sh
   ```

2. Restart daemon:
   ```bash
   systemctl --user restart odsc
   ```

3. Check for errors in logs:
   ```bash
   journalctl --user -u odsc --since "1 hour ago" | grep -i error
   ```

4. Run comprehensive test suite:
   ```bash
   python3 test_sync_scenarios.py
   ```

5. Report findings with:
   - Log excerpts showing the problem
   - Contents of `sync_test_results.txt` (created by test suite)
   - Output of `./diagnose_deletion.sh`

---

## Files Created

- `test_sync_scenarios.py` - Comprehensive interactive test suite
- `check_sync_status.sh` - Quick status checker
- `diagnose_deletion.sh` - Deletion-specific diagnostic
- `TESTING_INSTRUCTIONS.md` - This file

