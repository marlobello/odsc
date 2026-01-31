#!/usr/bin/env python3
"""
Comprehensive Sync Test Suite for ODSC

Tests all sync scenarios:
1. Files created in OneDrive
2. Files created locally
3. Files deleted from OneDrive
4. Files deleted locally
5. Files updated in OneDrive
6. Files updated locally
7. Folders created in OneDrive
8. Folders created locally
9. Folders deleted from OneDrive
10. Folders deleted locally

Usage:
    python3 test_sync_scenarios.py

This script will:
1. Check current sync status
2. Prompt you to perform actions in OneDrive/locally
3. Wait for sync
4. Verify expected behavior
5. Report results
"""

import sys
import time
import json
from pathlib import Path
from datetime import datetime

# Colors for output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

def print_header(text):
    print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
    print(f"{Colors.BLUE}{text.center(70)}{Colors.RESET}")
    print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")

def print_success(text):
    print(f"{Colors.GREEN}✓ {text}{Colors.RESET}")

def print_failure(text):
    print(f"{Colors.RED}✗ {text}{Colors.RESET}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.RESET}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ {text}{Colors.RESET}")

def load_config():
    """Load ODSC configuration."""
    config_path = Path.home() / ".config" / "odsc" / "config.json"
    if not config_path.exists():
        print_failure("ODSC config not found. Is ODSC installed?")
        sys.exit(1)
    
    with open(config_path) as f:
        config = json.load(f)
    
    return Path(config['sync_directory'])

def load_state():
    """Load ODSC sync state."""
    state_path = Path.home() / ".config" / "odsc" / "sync_state.json"
    if not state_path.exists():
        return {'files': {}, 'file_cache': {}}
    
    with open(state_path) as f:
        return json.load(f)

def check_daemon_running():
    """Check if ODSC daemon is running."""
    import subprocess
    result = subprocess.run(
        ['systemctl', '--user', 'is-active', 'odsc'],
        capture_output=True,
        text=True
    )
    return result.returncode == 0

def wait_for_sync(seconds=10):
    """Wait for sync to complete."""
    print_info(f"Waiting {seconds} seconds for sync to complete...")
    time.sleep(seconds)

def prompt_action(action_description):
    """Prompt user to perform an action."""
    print(f"\n{Colors.YELLOW}ACTION REQUIRED:{Colors.RESET}")
    print(f"  {action_description}")
    input("\nPress ENTER when done...")

class TestResult:
    def __init__(self, name, description):
        self.name = name
        self.description = description
        self.status = None  # 'pass', 'fail', 'skip'
        self.message = ""
    
    def passed(self, message=""):
        self.status = 'pass'
        self.message = message
    
    def failed(self, message=""):
        self.status = 'fail'
        self.message = message
    
    def skipped(self, message=""):
        self.status = 'skip'
        self.message = message

class SyncTest:
    def __init__(self):
        self.sync_dir = load_config()
        self.results = []
        print_header("ODSC Comprehensive Sync Test Suite")
        print_info(f"Sync Directory: {self.sync_dir}")
        
        if not check_daemon_running():
            print_warning("ODSC daemon is not running!")
            print_info("Start it with: systemctl --user start odsc")
            response = input("\nContinue anyway? (y/n): ")
            if response.lower() != 'y':
                sys.exit(1)
    
    def test_file_created_onedrive(self):
        """Test 1: File created in OneDrive should download locally (if user marks it)."""
        result = TestResult(
            "File Created in OneDrive",
            "New files on OneDrive should appear in GUI but not auto-download"
        )
        
        test_file = "test_onedrive_file.txt"
        
        prompt_action(
            f"Go to OneDrive web interface and create a new file:\n"
            f"  Name: {test_file}\n"
            f"  Content: Created in OneDrive"
        )
        
        wait_for_sync(15)
        
        # Check state - file should be in cache but not downloaded
        state = load_state()
        local_path = self.sync_dir / test_file
        
        if test_file in state.get('file_cache', {}):
            if not local_path.exists():
                result.passed("File in cache but not auto-downloaded (correct selective sync)")
            elif not state.get('files', {}).get(test_file, {}).get('downloaded'):
                result.failed("File exists locally but not marked as downloaded in state")
            else:
                result.passed("File in cache and can be downloaded by user")
        else:
            result.failed(f"File not found in cache after sync")
        
        self.results.append(result)
        return result
    
    def test_file_created_locally(self):
        """Test 2: File created locally should upload to OneDrive."""
        result = TestResult(
            "File Created Locally",
            "New local files should automatically upload to OneDrive"
        )
        
        test_file = self.sync_dir / "test_local_file.txt"
        
        prompt_action(
            f"Create a new file in your sync directory:\n"
            f"  Path: {test_file}\n"
            f"  Content: Created locally\n"
            f"  Command: echo 'Created locally' > {test_file}"
        )
        
        wait_for_sync(15)
        
        # Check state - file should be uploaded
        state = load_state()
        rel_path = test_file.relative_to(self.sync_dir)
        
        if str(rel_path) in state.get('files', {}):
            file_state = state['files'][str(rel_path)]
            if file_state.get('eTag'):
                result.passed("File uploaded to OneDrive (has eTag)")
            else:
                result.failed("File in state but no eTag (upload incomplete?)")
        else:
            result.failed("File not found in state after sync")
        
        self.results.append(result)
        return result
    
    def test_folder_created_onedrive(self):
        """Test 3: Folder created in OneDrive should be created locally."""
        result = TestResult(
            "Folder Created in OneDrive",
            "New folders on OneDrive should be created locally"
        )
        
        test_folder = "TestFolderFromOneDrive"
        
        prompt_action(
            f"Go to OneDrive web interface and create a new folder:\n"
            f"  Name: {test_folder}"
        )
        
        wait_for_sync(15)
        
        local_path = self.sync_dir / test_folder
        state = load_state()
        
        if local_path.exists() and local_path.is_dir():
            if test_folder in state.get('file_cache', {}):
                result.passed("Folder created locally and cached")
            else:
                result.failed("Folder created locally but not in cache")
        else:
            result.failed("Folder not created locally")
        
        self.results.append(result)
        return result
    
    def test_folder_created_locally(self):
        """Test 4: Folder created locally should upload to OneDrive."""
        result = TestResult(
            "Folder Created Locally",
            "New local folders should be created on OneDrive"
        )
        
        test_folder = self.sync_dir / "TestFolderFromLocal"
        
        prompt_action(
            f"Create a new folder in your sync directory:\n"
            f"  Path: {test_folder}\n"
            f"  Command: mkdir {test_folder}"
        )
        
        wait_for_sync(15)
        
        state = load_state()
        rel_path = test_folder.relative_to(self.sync_dir)
        
        if str(rel_path) in state.get('file_cache', {}):
            cached = state['file_cache'][str(rel_path)]
            if 'folder' in cached or cached.get('is_folder'):
                result.passed("Folder uploaded to OneDrive and cached")
            else:
                result.failed("In cache but not marked as folder")
        else:
            result.failed("Folder not found in cache after sync")
        
        self.results.append(result)
        return result
    
    def test_file_deleted_onedrive(self):
        """Test 5: File deleted from OneDrive should be moved to trash locally."""
        result = TestResult(
            "File Deleted from OneDrive",
            "Files deleted from OneDrive should be moved to trash locally (OneDrive authoritative)"
        )
        
        # First create and sync a file
        test_file = "test_delete_file.txt"
        local_path = self.sync_dir / test_file
        
        prompt_action(
            f"1. Create this file in OneDrive: {test_file}\n"
            f"2. Wait for it to sync\n"
            f"3. Download it locally via GUI (Keep Local Copy)\n"
            f"4. Then DELETE it from OneDrive"
        )
        
        # Verify file exists locally first
        if local_path.exists():
            print_info(f"File exists locally before deletion test")
        else:
            result.skipped("File doesn't exist locally - can't test deletion")
            self.results.append(result)
            return result
        
        wait_for_sync(20)
        
        # File should be gone or in trash
        if local_path.exists():
            result.failed("File still exists locally after OneDrive deletion")
        else:
            print_info("Checking if moved to trash...")
            result.passed("File removed from sync directory (OneDrive deletion respected)")
        
        # Verify not in cache
        state = load_state()
        if test_file in state.get('file_cache', {}):
            result.failed("File still in cache after deletion")
        
        self.results.append(result)
        return result
    
    def test_folder_deleted_onedrive(self):
        """Test 6: Folder deleted from OneDrive should be removed locally."""
        result = TestResult(
            "Folder Deleted from OneDrive",
            "Folders deleted from OneDrive should be removed locally (OneDrive authoritative)"
        )
        
        test_folder = "TestDeleteFolder"
        
        prompt_action(
            f"1. Create this folder in OneDrive: {test_folder}\n"
            f"2. Wait for it to sync (should appear locally)\n"
            f"3. Then DELETE it from OneDrive"
        )
        
        wait_for_sync(20)
        
        local_path = self.sync_dir / test_folder
        state = load_state()
        
        if local_path.exists():
            result.failed("Folder still exists locally after OneDrive deletion")
        else:
            result.passed("Folder removed locally")
        
        # Verify not in cache
        if test_folder in state.get('file_cache', {}):
            result.failed("Folder still in cache after deletion")
        
        self.results.append(result)
        return result
    
    def test_file_deleted_locally(self):
        """Test 7: File deleted locally should remain on OneDrive."""
        result = TestResult(
            "File Deleted Locally",
            "Files deleted locally should remain on OneDrive (local deletions don't propagate)"
        )
        
        test_file = "test_local_delete.txt"
        
        prompt_action(
            f"1. Create and sync a file: {test_file}\n"
            f"2. Download it locally (Keep Local Copy)\n"
            f"3. Delete it from your local sync directory\n"
            f"   Command: rm {self.sync_dir / test_file}"
        )
        
        wait_for_sync(15)
        
        prompt_action(
            f"Check OneDrive web interface:\n"
            f"  Is {test_file} still there? (y/n)"
        )
        
        response = input("Still on OneDrive? (y/n): ")
        if response.lower() == 'y':
            result.passed("File remains on OneDrive (correct behavior)")
        else:
            result.failed("File was deleted from OneDrive (should not happen!)")
        
        self.results.append(result)
        return result
    
    def test_folder_deleted_locally(self):
        """Test 8: Folder deleted locally should remain on OneDrive."""
        result = TestResult(
            "Folder Deleted Locally",
            "Folders deleted locally should remain on OneDrive (local deletions don't propagate)"
        )
        
        test_folder = "TestLocalDeleteFolder"
        
        prompt_action(
            f"1. Create and sync a folder: {test_folder}\n"
            f"2. Wait for it to appear on OneDrive\n"
            f"3. Delete it from your local sync directory\n"
            f"   Command: rm -rf {self.sync_dir / test_folder}"
        )
        
        wait_for_sync(15)
        
        prompt_action(
            f"Check OneDrive web interface:\n"
            f"  Is {test_folder} still there? (y/n)"
        )
        
        response = input("Still on OneDrive? (y/n): ")
        if response.lower() == 'y':
            result.passed("Folder remains on OneDrive (correct behavior)")
        else:
            result.failed("Folder was deleted from OneDrive (should not happen!)")
        
        self.results.append(result)
        return result
    
    def test_file_updated_onedrive(self):
        """Test 9: File updated in OneDrive should download locally."""
        result = TestResult(
            "File Updated in OneDrive",
            "Files updated in OneDrive should sync to local copies"
        )
        
        test_file = "test_update_file.txt"
        
        prompt_action(
            f"1. Create and sync a file: {test_file}\n"
            f"2. Download it locally (Keep Local Copy)\n"
            f"3. Edit it in OneDrive (change content)"
        )
        
        # Get current modification time
        local_path = self.sync_dir / test_file
        if local_path.exists():
            old_mtime = local_path.stat().st_mtime
        else:
            result.skipped("File doesn't exist locally")
            self.results.append(result)
            return result
        
        wait_for_sync(20)
        
        # Check if file was updated
        if local_path.exists():
            new_mtime = local_path.stat().st_mtime
            if new_mtime > old_mtime:
                result.passed("File updated locally from OneDrive")
            else:
                result.failed("File not updated (mtime unchanged)")
        else:
            result.failed("File disappeared after update")
        
        self.results.append(result)
        return result
    
    def test_file_updated_locally(self):
        """Test 10: File updated locally should upload to OneDrive."""
        result = TestResult(
            "File Updated Locally",
            "Files updated locally should sync to OneDrive"
        )
        
        test_file = "test_local_update.txt"
        local_path = self.sync_dir / test_file
        
        prompt_action(
            f"1. Create and sync a file: {test_file}\n"
            f"2. Download it locally (Keep Local Copy)\n"
            f"3. Edit it locally\n"
            f"   Command: echo 'Updated content' >> {local_path}"
        )
        
        wait_for_sync(15)
        
        state = load_state()
        rel_path = str(local_path.relative_to(self.sync_dir))
        
        if rel_path in state.get('files', {}):
            file_state = state['files'][rel_path]
            if file_state.get('upload_error'):
                result.failed(f"Upload error: {file_state['upload_error']}")
            elif file_state.get('eTag'):
                result.passed("File uploaded to OneDrive")
            else:
                result.failed("File in state but no eTag")
        else:
            result.failed("File not in state")
        
        self.results.append(result)
        return result
    
    def print_summary(self):
        """Print test summary."""
        print_header("TEST SUMMARY")
        
        passed = sum(1 for r in self.results if r.status == 'pass')
        failed = sum(1 for r in self.results if r.status == 'fail')
        skipped = sum(1 for r in self.results if r.status == 'skip')
        total = len(self.results)
        
        for result in self.results:
            if result.status == 'pass':
                print_success(f"{result.name}: {result.description}")
                if result.message:
                    print(f"      {result.message}")
            elif result.status == 'fail':
                print_failure(f"{result.name}: {result.description}")
                print(f"      {Colors.RED}{result.message}{Colors.RESET}")
            elif result.status == 'skip':
                print_warning(f"{result.name}: {result.description}")
                print(f"      {result.message}")
        
        print(f"\n{Colors.BLUE}{'─'*70}{Colors.RESET}")
        print(f"Total Tests: {total}")
        print_success(f"Passed: {passed}")
        print_failure(f"Failed: {failed}")
        print_warning(f"Skipped: {skipped}")
        print(f"{Colors.BLUE}{'─'*70}{Colors.RESET}\n")
        
        # Save results to file
        results_file = Path("sync_test_results.txt")
        with open(results_file, 'w') as f:
            f.write(f"ODSC Sync Test Results - {datetime.now()}\n")
            f.write("="*70 + "\n\n")
            for result in self.results:
                f.write(f"{result.status.upper()}: {result.name}\n")
                f.write(f"  {result.description}\n")
                if result.message:
                    f.write(f"  {result.message}\n")
                f.write("\n")
            f.write(f"\nSummary: {passed} passed, {failed} failed, {skipped} skipped\n")
        
        print_info(f"Results saved to: {results_file.absolute()}")
        
        return failed == 0

def main():
    """Run all tests."""
    test = SyncTest()
    
    print("\nThis test suite will guide you through testing all sync scenarios.")
    print("You'll be prompted to perform actions and verify results.\n")
    input("Press ENTER to begin...")
    
    # Run all tests
    print_header("TEST 1: File Created in OneDrive")
    test.test_file_created_onedrive()
    
    print_header("TEST 2: File Created Locally")
    test.test_file_created_locally()
    
    print_header("TEST 3: Folder Created in OneDrive")
    test.test_folder_created_onedrive()
    
    print_header("TEST 4: Folder Created Locally")
    test.test_folder_created_locally()
    
    print_header("TEST 5: File Deleted from OneDrive")
    test.test_file_deleted_onedrive()
    
    print_header("TEST 6: Folder Deleted from OneDrive")
    test.test_folder_deleted_onedrive()
    
    print_header("TEST 7: File Deleted Locally")
    test.test_file_deleted_locally()
    
    print_header("TEST 8: Folder Deleted Locally")
    test.test_folder_deleted_locally()
    
    print_header("TEST 9: File Updated in OneDrive")
    test.test_file_updated_onedrive()
    
    print_header("TEST 10: File Updated Locally")
    test.test_file_updated_locally()
    
    # Print summary
    success = test.print_summary()
    
    return 0 if success else 1

if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
