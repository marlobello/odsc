#!/usr/bin/env python3
"""Tests for secure path handling helpers."""

from pathlib import Path

import pytest

from odsc.path_utils import (
    SecurityError,
    cleanup_empty_parent_dirs,
    extract_item_path,
    sanitize_onedrive_path,
    validate_sync_path,
)


def test_sanitize_onedrive_path_rejects_traversal_components():
    with pytest.raises(SecurityError, match="forbidden component"):
        sanitize_onedrive_path("/drive/root:/Documents/../secret.txt")

    with pytest.raises(SecurityError, match="forbidden component"):
        sanitize_onedrive_path("/drive/root:/Documents/./secret.txt")


def test_extract_item_path_rejects_unsafe_names():
    item = {
        "name": "nested/file.txt",
        "parentReference": {"path": "/drive/root:/Documents"},
    }

    with pytest.raises(SecurityError, match="path separator or null byte"):
        extract_item_path(item)


def test_validate_sync_path_rejects_symlink_before_resolution(tmp_path):
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    target = sync_dir / "target.txt"
    target.write_text("data")
    symlink = sync_dir / "linked.txt"
    symlink.symlink_to(target)

    with pytest.raises(SecurityError, match="Symlink detected"):
        validate_sync_path("linked.txt", sync_dir)


def test_cleanup_empty_parent_dirs_rejects_paths_outside_sync_root(tmp_path):
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    outside_dir = tmp_path / "outside" / "nested"
    outside_dir.mkdir(parents=True)
    file_path = outside_dir / "file.txt"

    with pytest.raises(SecurityError, match="outside sync root"):
        cleanup_empty_parent_dirs(file_path, sync_dir)

    assert outside_dir.exists()
    assert outside_dir.parent.exists()


def test_extract_item_path_builds_safe_relative_path():
    item = {
        "name": "file.txt",
        "parentReference": {"path": "/drive/root:/Documents/Reports"},
    }

    assert extract_item_path(item) == str(Path("Documents") / "Reports" / "file.txt")
