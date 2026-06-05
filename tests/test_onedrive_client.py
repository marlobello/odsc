#!/usr/bin/env python3
"""Tests for OneDrive client download behavior."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import requests

from odsc.error_handling import is_transient_error
from odsc.onedrive_client import (
    IntegrityVerificationError,
    OneDriveClient,
    _RetryAfterWait,
    _parse_retry_after_header,
)
from odsc.quickxorhash import quickxorhash_bytes


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


class FakeJsonResponse:
    """Minimal JSON response stub for upload tests."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


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


def test_download_file_verifies_matching_quickxorhash(tmp_path, monkeypatch):
    """Downloads with matching OneDrive QuickXorHash are accepted."""
    client = OneDriveClient(token_data={"access_token": "token", "expires_at": 10**12})
    destination = tmp_path / "verified.txt"
    content = b"verified download"
    remote_hash = quickxorhash_bytes(content)

    monkeypatch.setattr(
        client,
        "get_file_metadata",
        lambda file_id: {"id": file_id, "file": {"hashes": {"quickXorHash": remote_hash}}},
    )
    monkeypatch.setattr(
        client,
        "_api_request",
        lambda method, endpoint, **kwargs: FakeResponse([content]),
    )

    metadata = client.download_file("file-id", destination)

    assert metadata["file"]["hashes"]["quickXorHash"] == remote_hash
    assert destination.read_bytes() == content
    assert not list(tmp_path.glob("*.odsc_tmp"))


def test_download_file_rejects_quickxorhash_mismatch_without_replacing(tmp_path, monkeypatch):
    """Hash mismatches fail before replacing an existing destination."""
    client = OneDriveClient(token_data={"access_token": "token", "expires_at": 10**12})
    destination = tmp_path / "verified.txt"
    destination.write_bytes(b"stable contents")
    corrupt_content = b"corrupt download"
    remote_hash = quickxorhash_bytes(b"expected content")

    monkeypatch.setattr(
        client,
        "get_file_metadata",
        lambda file_id: {"id": file_id, "hashes": {"quickXorHash": remote_hash}},
    )
    monkeypatch.setattr(
        client,
        "_api_request",
        lambda method, endpoint, **kwargs: FakeResponse([corrupt_content]),
    )

    with pytest.raises(IntegrityVerificationError):
        OneDriveClient.download_file.__wrapped__(client, "file-id", destination)

    assert destination.read_bytes() == b"stable contents"
    assert not list(tmp_path.glob("*.odsc_tmp"))


def test_download_file_skips_verification_without_quickxorhash(tmp_path, monkeypatch):
    """Downloads without a remote QuickXorHash keep existing behavior."""
    client = OneDriveClient(token_data={"access_token": "token", "expires_at": 10**12})
    destination = tmp_path / "unhashed.txt"
    content = b"download without hash"

    monkeypatch.setattr(client, "get_file_metadata", lambda file_id: {"id": file_id})
    monkeypatch.setattr(
        client,
        "_api_request",
        lambda method, endpoint, **kwargs: FakeResponse([content]),
    )

    metadata = client.download_file("file-id", destination)

    assert metadata == {"id": "file-id"}
    assert destination.read_bytes() == content


def test_upload_file_verifies_matching_quickxorhash(tmp_path, monkeypatch):
    """Uploads with matching response QuickXorHash are accepted."""
    client = OneDriveClient(token_data={"access_token": "token", "expires_at": 10**12})
    local_path = tmp_path / "upload.txt"
    local_path.write_bytes(b"verified upload")
    remote_hash = quickxorhash_bytes(local_path.read_bytes())

    monkeypatch.setattr(
        client,
        "_api_request",
        lambda method, endpoint, **kwargs: FakeJsonResponse(
            {"id": "remote-id", "file": {"hashes": {"quickXorHash": remote_hash}}}
        ),
    )

    metadata = OneDriveClient.upload_file.__wrapped__(client, local_path, "upload.txt")

    assert metadata["file"]["hashes"]["quickXorHash"] == remote_hash


def test_upload_file_rejects_quickxorhash_mismatch(tmp_path, monkeypatch):
    """Uploads fail when OneDrive reports a different QuickXorHash."""
    client = OneDriveClient(token_data={"access_token": "token", "expires_at": 10**12})
    local_path = tmp_path / "upload.txt"
    local_path.write_bytes(b"verified upload")
    remote_hash = quickxorhash_bytes(b"different content")

    monkeypatch.setattr(
        client,
        "_api_request",
        lambda method, endpoint, **kwargs: FakeJsonResponse(
            {"id": "remote-id", "quickXorHash": remote_hash}
        ),
    )

    with pytest.raises(IntegrityVerificationError):
        OneDriveClient.upload_file.__wrapped__(client, local_path, "upload.txt")


def test_upload_file_skips_verification_without_quickxorhash(tmp_path, monkeypatch):
    """Upload responses without QuickXorHash keep existing behavior."""
    client = OneDriveClient(token_data={"access_token": "token", "expires_at": 10**12})
    local_path = tmp_path / "upload.txt"
    local_path.write_bytes(b"upload without hash")

    monkeypatch.setattr(
        client,
        "_api_request",
        lambda method, endpoint, **kwargs: FakeJsonResponse({"id": "remote-id"}),
    )

    metadata = OneDriveClient.upload_file.__wrapped__(client, local_path, "upload.txt")

    assert metadata == {"id": "remote-id"}


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


def _http_error(status_code, headers=None):
    response = requests.Response()
    response.status_code = status_code
    response.headers.update(headers or {})
    return requests.exceptions.HTTPError("request failed", response=response)


def test_http_429_is_transient_error():
    """Graph throttling responses should be retried."""
    assert is_transient_error(_http_error(429))


def test_parse_retry_after_header_seconds_date_past_and_cap():
    """Retry-After supports delay-seconds and HTTP-date values."""
    now = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    future = format_datetime(now + timedelta(seconds=45), usegmt=True)
    past = format_datetime(now - timedelta(seconds=5), usegmt=True)

    assert _parse_retry_after_header("42", now=now) == 42.0
    assert _parse_retry_after_header(future, now=now) == 45.0
    assert _parse_retry_after_header(past, now=now) == 0.0
    assert _parse_retry_after_header("999", now=now, max_delay=300) == 300.0


def test_retry_after_wait_honors_429_retry_after_header():
    """Tenacity wait should be at least the server's Retry-After delay."""
    error = _http_error(429, {"Retry-After": "12"})

    class FakeOutcome:
        def exception(self):
            return error

    class FakeRetryState:
        outcome = FakeOutcome()

    wait_strategy = _RetryAfterWait(lambda retry_state: 1.0)

    assert wait_strategy(FakeRetryState()) >= 12.0
