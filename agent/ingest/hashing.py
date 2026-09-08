"""Content hashing.

SHA-256 over the bytes is an artefact's identity. Everything else about a file —
its name, where it sits, when it arrived — is metadata that can be wrong or can
change; the hash cannot. It gives deduplication for free, which matters because
people photograph the same script twice and download the same pathology PDF
three times.

Hashing is streamed. A 40 MB scanned letter must not be held in memory twice on
its way into the vault, and the copy into ``raw/`` hashes as it goes rather than
reading the file a second time.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

HASH_ALGO = "sha256"
HASH_LENGTH = 64

#: 1 MiB. Large enough that a big PDF is a handful of reads, small enough that
#: a phone photo does not round up to a wasteful allocation.
CHUNK_BYTES = 1024 * 1024

#: How much of the head to keep while streaming, for mime sniffing. Format magic
#: lives in the first few dozen bytes; the rest is room for the text heuristics.
HEAD_BYTES = 8192


def is_valid_hash(value: object) -> bool:
    """True if *value* is a full lowercase hex SHA-256 digest."""
    return (
        isinstance(value, str)
        and len(value) == HASH_LENGTH
        and all(c in "0123456789abcdef" for c in value)
    )


def is_hash_prefix(value: object) -> bool:
    """True if *value* could be the leading characters of a digest."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= HASH_LENGTH
        and all(c in "0123456789abcdef" for c in value)
    )


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> tuple[str, int]:
    """Return ``(digest, size)`` for the file at *path*, reading it once."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def copy_and_hash(source: BinaryIO, destination: BinaryIO) -> tuple[str, int, bytes]:
    """Stream *source* into *destination*, hashing on the way through.

    Returns ``(digest, size, head)``. The head is kept because the same pass has
    to serve mime sniffing: reading the file again to look at its first bytes
    would be a second full open on a path that may be on a virtual drive.
    """
    digest = hashlib.sha256()
    size = 0
    head = b""
    while chunk := source.read(CHUNK_BYTES):
        digest.update(chunk)
        size += len(chunk)
        if len(head) < HEAD_BYTES:
            head += chunk[: HEAD_BYTES - len(head)]
        destination.write(chunk)
    return digest.hexdigest(), size, head
