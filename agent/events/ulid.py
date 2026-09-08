"""ULID generation and parsing.

A ULID is 128 bits: a 48-bit big-endian millisecond timestamp followed by 80
bits of randomness, rendered as 26 characters of Crockford base32. Two
properties are why the event log uses them:

* They sort lexically in time order, so a merged read has a total order that
  needs no coordination between devices.
* They collide with negligible probability without a central allocator, so a
  laptop and a phone that have never seen each other can both append.

Note on the spec: the example id in CLAUDE.md is 22 characters, which is not a
valid ULID. It reads as illustrative; this module implements the real 26
character form.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

# Crockford base32: the digits, minus I, L, O and U.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}

ULID_LENGTH = 26
_TIME_CHARS = 10
_RANDOM_BITS = 80
_MAX_RANDOM = (1 << _RANDOM_BITS) - 1
_MAX_TIMESTAMP_MS = (1 << 48) - 1

_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new() -> str:
    """Return a fresh ULID from the current clock.

    Within a single millisecond the random component is incremented rather than
    redrawn, so ids from one process are strictly increasing and never tie. A
    tie would leave the union read's sort order dependent on file discovery
    order, which is the one thing it must not be.
    """
    global _last_ms, _last_random

    with _lock:
        ms = int(time.time() * 1000)
        if not 0 <= ms <= _MAX_TIMESTAMP_MS:
            raise ValueError(f"timestamp out of ULID range: {ms}")

        if ms == _last_ms:
            if _last_random >= _MAX_RANDOM:
                # Astronomically unlikely; roll into the next millisecond
                # rather than emit a duplicate.
                ms += 1
                randomness = int.from_bytes(os.urandom(10), "big")
            else:
                randomness = _last_random + 1
        elif ms < _last_ms:
            # Wall clock went backwards (NTP step, VM resume). Keep issuing
            # under the last millisecond so ids stay monotonic for this process.
            ms = _last_ms
            randomness = _last_random + 1 if _last_random < _MAX_RANDOM else 0
        else:
            randomness = int.from_bytes(os.urandom(10), "big")

        _last_ms = ms
        _last_random = randomness

    return encode(ms, randomness)


def from_timestamp(timestamp_ms: int) -> str:
    """Return a ULID stamped with an explicit millisecond.

    Deliberately separate from :func:`new`: it does not touch the monotonic
    clock guard, because backfilling an id for a past instant must not make the
    next live id jump forwards.
    """
    ms = int(timestamp_ms)
    if not 0 <= ms <= _MAX_TIMESTAMP_MS:
        raise ValueError(f"timestamp out of ULID range: {ms}")
    return encode(ms, int.from_bytes(os.urandom(10), "big"))


def encode(ms: int, randomness: int) -> str:
    return _encode(ms, _TIME_CHARS) + _encode(randomness, ULID_LENGTH - _TIME_CHARS)


def is_valid(value: object) -> bool:
    """True if *value* is a canonical 26-character Crockford base32 ULID."""
    if not isinstance(value, str) or len(value) != ULID_LENGTH:
        return False
    if any(c not in _DECODE for c in value):
        return False
    # 26 base32 characters hold 130 bits; a ULID is 128, so the leading
    # character cannot exceed 7.
    return _DECODE[value[0]] <= 7


def timestamp_ms(value: str) -> int:
    """Return the millisecond timestamp embedded in *value*."""
    if not is_valid(value):
        raise ValueError(f"not a valid ULID: {value!r}")
    ms = 0
    for c in value[:_TIME_CHARS]:
        ms = (ms << 5) | _DECODE[c]
    return ms


def timestamp(value: str) -> datetime:
    """Return the embedded timestamp as a timezone-aware UTC datetime."""
    return datetime.fromtimestamp(timestamp_ms(value) / 1000, tz=timezone.utc)
