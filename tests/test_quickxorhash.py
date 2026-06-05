"""Tests for the QuickXorHash implementation (OneDrive content hash)."""

import base64

from odsc.quickxorhash import (
    QuickXorHash,
    quickxorhash_bytes,
    quickxorhash_file,
    extract_quickxorhash,
)


def test_empty_input_is_twenty_zero_bytes():
    """An empty input hashes to 20 zero bytes (length 0 XORs in nothing)."""
    digest = QuickXorHash().digest()
    assert digest == b"\x00" * 20
    assert quickxorhash_bytes(b"") == base64.b64encode(b"\x00" * 20).decode()


def test_digest_is_twenty_bytes():
    assert len(QuickXorHash().update(b"hello world").digest()) == 20


def test_deterministic():
    assert quickxorhash_bytes(b"the quick brown fox") == quickxorhash_bytes(b"the quick brown fox")


def test_distinguishes_content():
    assert quickxorhash_bytes(b"content A") != quickxorhash_bytes(b"content B")


def test_length_sensitive():
    # Same bytes, different length -> different hash (length is folded in).
    assert quickxorhash_bytes(b"abc") != quickxorhash_bytes(b"abc\x00")


def test_streaming_matches_single_update():
    data = bytes(range(256)) * 17  # 4352 bytes, spans multiple 160-byte cycles
    one_shot = QuickXorHash()
    one_shot.update(data)

    chunked = QuickXorHash()
    for i in range(0, len(data), 7):  # awkward chunk size to stress offsets
        chunked.update(data[i:i + 7])

    assert one_shot.digest() == chunked.digest()


def test_file_helper_matches_bytes(tmp_path):
    data = b"x" * 5000 + b"payload" + b"y" * 1234
    f = tmp_path / "blob.bin"
    f.write_bytes(data)
    assert quickxorhash_file(f) == quickxorhash_bytes(data)


def test_extract_quickxorhash_from_graph_item():
    item = {"file": {"hashes": {"quickXorHash": "ABC123=="}}}
    assert extract_quickxorhash(item) == "ABC123=="


def test_extract_quickxorhash_handles_missing():
    assert extract_quickxorhash({"folder": {}}) is None
    assert extract_quickxorhash({"file": {}}) is None
    assert extract_quickxorhash({}) is None
    assert extract_quickxorhash(None) is None
