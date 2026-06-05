#!/usr/bin/env python3
"""Tests for `odsc update` installer integrity verification."""

import hashlib
import json
import subprocess
import types

from odsc import cli


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


def _patch_release(monkeypatch, tag="v999.0.0"):
    monkeypatch.setattr(
        cli.urllib.request,
        "urlopen",
        lambda req, timeout=10: _FakeResp(json.dumps({"tag_name": tag}).encode()),
    )


def _capture_subprocess(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: calls.append(a) or types.SimpleNamespace(returncode=0),
    )
    return calls


def test_update_aborts_on_checksum_mismatch(monkeypatch):
    _patch_release(monkeypatch)
    monkeypatch.setattr(cli, "_download_bytes", lambda url, timeout=30: b"#!/bin/bash\n")
    monkeypatch.setattr(cli, "_fetch_expected_sha256", lambda url: "0" * 64)
    calls = _capture_subprocess(monkeypatch)

    rc = cli.cmd_update(types.SimpleNamespace(yes=False))

    assert rc == 1
    assert calls == []  # installer must NOT run on mismatch


def test_update_runs_when_checksum_verifies(monkeypatch):
    script = b"#!/bin/bash\necho installing\n"
    _patch_release(monkeypatch)
    monkeypatch.setattr(cli, "_download_bytes", lambda url, timeout=30: script)
    monkeypatch.setattr(cli, "_fetch_expected_sha256", lambda url: hashlib.sha256(script).hexdigest())
    calls = _capture_subprocess(monkeypatch)

    rc = cli.cmd_update(types.SimpleNamespace(yes=False))

    assert rc == 0
    assert len(calls) == 1


def test_update_without_checksum_is_cancelled_non_interactively(monkeypatch):
    _patch_release(monkeypatch)
    monkeypatch.setattr(cli, "_download_bytes", lambda url, timeout=30: b"#!/bin/bash\n")
    monkeypatch.setattr(cli, "_fetch_expected_sha256", lambda url: None)
    calls = _capture_subprocess(monkeypatch)

    # Non-interactive (no TTY) and no --yes -> refuse to run.
    rc = cli.cmd_update(types.SimpleNamespace(yes=False))

    assert rc == 1
    assert calls == []


def test_update_without_checksum_runs_with_yes(monkeypatch):
    _patch_release(monkeypatch)
    monkeypatch.setattr(cli, "_download_bytes", lambda url, timeout=30: b"#!/bin/bash\n")
    monkeypatch.setattr(cli, "_fetch_expected_sha256", lambda url: None)
    calls = _capture_subprocess(monkeypatch)

    rc = cli.cmd_update(types.SimpleNamespace(yes=True))

    assert rc == 0
    assert len(calls) == 1
