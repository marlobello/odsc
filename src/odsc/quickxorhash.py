"""QuickXorHash — OneDrive's content hash algorithm.

Microsoft OneDrive (personal) reports a ``quickXorHash`` for every file in its
item metadata (``item['file']['hashes']['quickXorHash']``). Computing the same
hash locally lets ODSC detect *real content changes* deterministically, instead
of relying on the fragile ``(mtime, size)`` heuristic — which produces spurious
uploads on touch, misses same-size edits, and causes download→upload echo.

This is a faithful port of Microsoft's reference QuickXorHash:
a 160-bit XOR-based hash with an 11-bit circular shift, finalized by XORing the
little-endian byte length into the trailing 8 bytes. The result is returned
base64-encoded to match the string OneDrive provides.
"""

from __future__ import annotations

import base64
from pathlib import Path

_WIDTH_IN_BITS = 160
_SHIFT = 11
_BITS_IN_LAST_CELL = 32
_CELL_COUNT = (_WIDTH_IN_BITS - 1) // 64 + 1  # 3 x 64-bit cells
_MASK64 = (1 << 64) - 1


class QuickXorHash:
    """Incremental QuickXorHash computer.

    Feed bytes with :meth:`update` and read the result with :meth:`digest`
    (raw 20 bytes) or :meth:`base64digest` (the form OneDrive reports).
    """

    def __init__(self) -> None:
        self._data = [0] * _CELL_COUNT
        self._length_so_far = 0
        self._shift_so_far = 0

    def update(self, data: bytes) -> "QuickXorHash":
        """Add *data* to the running hash."""
        if not data:
            return self

        cb_size = len(data)
        vector_array_index = self._shift_so_far // 64
        vector_offset = self._shift_so_far % 64
        iterations = min(cb_size, _WIDTH_IN_BITS)

        for i in range(iterations):
            is_last_cell = vector_array_index == _CELL_COUNT - 1
            bits_in_vector_cell = _BITS_IN_LAST_CELL if is_last_cell else 64

            if vector_offset <= bits_in_vector_cell - 8:
                xored = 0
                for j in range(i, cb_size, _WIDTH_IN_BITS):
                    xored ^= data[j]
                self._data[vector_array_index] ^= (xored << vector_offset) & _MASK64
            else:
                index1 = vector_array_index
                index2 = 0 if is_last_cell else vector_array_index + 1
                low = bits_in_vector_cell - vector_offset

                xored_byte = 0
                for j in range(i, cb_size, _WIDTH_IN_BITS):
                    xored_byte ^= data[j]
                self._data[index1] ^= (xored_byte << vector_offset) & _MASK64
                self._data[index2] ^= (xored_byte >> low) & _MASK64

            vector_offset += _SHIFT
            while vector_offset >= bits_in_vector_cell:
                vector_array_index = 0 if is_last_cell else vector_array_index + 1
                vector_offset -= bits_in_vector_cell

        self._shift_so_far = (self._shift_so_far + _SHIFT * (cb_size % _WIDTH_IN_BITS)) % _WIDTH_IN_BITS
        self._length_so_far += cb_size
        return self

    def digest(self) -> bytes:
        """Return the raw 20-byte hash."""
        rgb = bytearray((_WIDTH_IN_BITS - 1) // 8 + 1)  # 20 bytes

        # First (CELL_COUNT - 1) cells contribute 8 bytes each; the last cell
        # contributes only the remaining bytes (4, for the 32-bit last cell).
        for i in range(_CELL_COUNT - 1):
            rgb[i * 8:i * 8 + 8] = (self._data[i] & _MASK64).to_bytes(8, "little")

        last = (_CELL_COUNT - 1) * 8
        remaining = len(rgb) - last
        rgb[last:last + remaining] = (self._data[-1] & _MASK64).to_bytes(8, "little")[:remaining]

        # XOR the total length (little-endian, signed 64-bit) into the last 8 bytes.
        length_bytes = (self._length_so_far & _MASK64).to_bytes(8, "little")
        start = (_WIDTH_IN_BITS // 8) - len(length_bytes)  # 20 - 8 = 12
        for i in range(len(length_bytes)):
            rgb[start + i] ^= length_bytes[i]

        return bytes(rgb)

    def base64digest(self) -> str:
        """Return the base64-encoded hash, as OneDrive reports it."""
        return base64.b64encode(self.digest()).decode("ascii")


def quickxorhash_bytes(data: bytes) -> str:
    """Return the base64 QuickXorHash of *data*."""
    return QuickXorHash().update(data).base64digest()


def quickxorhash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Return the base64 QuickXorHash of the file at *path* (streamed)."""
    hasher = QuickXorHash()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.base64digest()


def extract_quickxorhash(item: dict) -> "str | None":
    """Pull ``quickXorHash`` out of a Graph item's metadata, if present.

    Handles the full Graph shape (``item['file']['hashes']['quickXorHash']``),
    a top-level ``hashes`` dict, and the flat ``quickXorHash`` key that ODSC
    stores in its reduced cache/state entries.
    """
    if not isinstance(item, dict):
        return None
    file_facet = item.get("file")
    if isinstance(file_facet, dict):
        hashes = file_facet.get("hashes")
        if isinstance(hashes, dict):
            value = hashes.get("quickXorHash")
            if value:
                return value
    hashes = item.get("hashes")
    if isinstance(hashes, dict):
        value = hashes.get("quickXorHash")
        if value:
            return value
    # Flat key (ODSC reduced cache/state shape).
    return item.get("quickXorHash") or None
