#!/bin/bash
# Diagnose folder deletion issues

echo "═══════════════════════════════════════════════════════════════"
echo "FOLDER DELETION DIAGNOSTIC"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Check if new code is deployed
echo "1. Checking deployed code version..."
if grep -q "_verify_and_retry_deletions" /home/marlo/git/odsc/src/odsc/daemon.py; then
    echo "   ✓ Latest deletion fix code is present"
else
    echo "   ✗ Deletion fix code NOT found - daemon not updated!"
fi
echo ""

# Check if daemon is using the updated code
echo "2. Checking running daemon..."
DAEMON_PID=$(systemctl --user show odsc -p MainPID --value)
if [ -n "$DAEMON_PID" ] && [ "$DAEMON_PID" != "0" ]; then
    DAEMON_START=$(systemctl --user show odsc -p ActiveEnterTimestamp --value)
    CODE_MODIFIED=$(stat -c %y /home/marlo/git/odsc/src/odsc/daemon.py | cut -d'.' -f1)
    echo "   Daemon PID: $DAEMON_PID"
    echo "   Daemon started: $DAEMON_START"
    echo "   Code modified: $CODE_MODIFIED"
    echo ""
    echo "   ⚠ If code was modified AFTER daemon start, restart needed!"
    echo "   Command: systemctl --user restart odsc"
else
    echo "   ✗ Daemon not running"
fi
echo ""

# Check logs for deletion attempts
echo "3. Checking logs for deletion activity..."
echo "   Recent deletion-related logs:"
journalctl --user -u odsc --since "10 minutes ago" --no-pager | grep -i "delet" | tail -10
echo ""

# Check for specific error patterns
echo "4. Checking for known issues..."
journalctl --user -u odsc --since "30 minutes ago" --no-pager | grep -E "(Creating folder|Skipping upload.*deleted|verify.*deletion|retry)" | tail -15
echo ""

echo "═══════════════════════════════════════════════════════════════"
echo "RECOMMENDATIONS:"
echo "═══════════════════════════════════════════════════════════════"
echo "1. If code was updated recently: systemctl --user restart odsc"
echo "2. Watch live logs: journalctl --user -u odsc -f | grep -i delet"
echo "3. Force a sync: touch ~/.config/odsc/.force_sync"
echo "4. Run comprehensive test: python3 test_sync_scenarios.py"
echo ""

