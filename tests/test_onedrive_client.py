#!/usr/bin/env python3
"""Tests for OneDrive client download behavior."""

import pytest

from odsc.onedrive_client import OneDriveClient


class FakeResponse:
    """Minimal streaming response stub for download tests."""

    def __init__(self, chunks, error=None):
        self._chunks = chunks
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_content(self, chunk_size=65536):
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error


class FakeRequestResponse:
    """Minimal response stub for request-header tests."""

    ok = True
    status_code = 200

    def raise_for_status(self):
        return None


def test_download_file_writes_to_temp_then_replaces(tmp_path, monkeypatch):
    """Successful downloads should atomically replace the destination file."""
    client = OneDriveClient(token_data={"access_token": "token", "expires_at": 10**12})
    destination = tmp_path / "nested" / "file.txt"

    monkeypatch.setattr(client, "get_file_metadata", lambda file_id: {"id": file_id, "eTag": "etag"})
    monkeypatch.setattr(
        client,
        "_api_request",
        lambda method, endpoint, **kwargs: FakeResponse([b"hello ", b"world"]),
    )

    metadata = client.download_file("file-id", destination)

    assert metadata["eTag"] == "etag"
    assert destination.read_bytes() == b"hello world"
    assert destination.parent.is_dir()
    assert not list(destination.parent.glob("*.odsc_tmp"))


def test_download_file_failure_keeps_existing_file_and_cleans_temp(tmp_path, monkeypatch):
    """Failed downloads must not leave a partial destination behind."""
    client = OneDriveClient(token_data={"access_token": "token", "expires_at": 10**12})
    destination = tmp_path / "file.txt"
    destination.write_bytes(b"stable contents")

    monkeypatch.setattr(client, "get_file_metadata", lambda file_id: {"id": file_id, "eTag": "etag"})
    monkeypatch.setattr(
        client,
        "_api_request",
        lambda method, endpoint, **kwargs: FakeResponse([b"partial"], error=ConnectionError("boom")),
    )

    with pytest.raises(ConnectionError):
        client.download_file("file-id", destination)

    assert destination.read_bytes() == b"stable contents"
    assert not list(tmp_path.glob("*.odsc_tmp"))


def test_api_request_uses_cached_access_token(monkeypatch):
    """Header creation should not depend on reading ``token_data`` directly."""
    client = OneDriveClient(token_data={"access_token": "token", "expires_at": 10**12})
    captured = {}

    monkeypatch.setattr(client, "_ensure_token", lambda: None)
    client.token_data = {}

    def fake_request(method, url, headers=None, **kwargs):
        captured["authorization"] = headers["Authorization"]
        return FakeRequestResponse()

    monkeypatch.setattr(client._session, "request", fake_request)

    client._api_request("GET", "/me")

    assert captured["authorization"] == "Bearer token"
