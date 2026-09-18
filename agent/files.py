"""Writing bytes that are the same bytes on every computer.

Windows opens files in **text mode** unless told otherwise, and that includes
``os.open``: the translation happens inside ``os.write``, so code that never
touches a text wrapper and passes only ``bytes`` still has every ``\\n`` turned
into ``\\r\\n`` on the way to the disk. Nothing in the call reads as textual,
which is why it survived being specified against since phase 3 and was found
only when the suite first ran on Windows.

What it costs here is not cosmetic:

- **The event log** gains a carriage return on every line, so a shard written on
  Windows differs byte for byte from the same shard written anywhere else.
- **The wiki** is regenerated from that log, so two machines sharing one folder
  rebuild it into different bytes, the writer's manifest sees every file as
  changed, and invariant 1 — delete everything derived, replay, get the same
  bytes back — stops meaning anything across machines.
- **Raw artefacts** are worse than different: a photograph or a PDF staged
  through a text-mode descriptor has every ``0x0A`` byte in it rewritten, which
  is a corrupted document with a hash that no longer matches what was read.

So every descriptor this program writes through is opened with
:data:`O_BINARY`, which is zero everywhere but Windows, and every text file it
writes goes out as encoded bytes with explicit newlines.
``tests/test_writes_are_binary.py`` walks the package and fails on a writer that
does neither.
"""

from __future__ import annotations

import os
from pathlib import Path

#: ``os.O_BINARY`` on Windows, and nothing at all anywhere else — the flag does
#: not exist on POSIX because there is no translation to turn off.
O_BINARY = getattr(os, "O_BINARY", 0)


def write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Replace *path* with exactly *data*, creating it with *mode*."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_BINARY, mode)
    try:
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
        os.fsync(fd)
    finally:
        os.close(fd)


def write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Write *text* as UTF-8 with the newlines it already has.

    ``Path.write_text`` would translate them on Windows, which is the whole
    subject of this module.
    """
    write_bytes(path, text.encode("utf-8"), mode)
