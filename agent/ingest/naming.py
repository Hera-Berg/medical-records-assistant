"""The filename grammar for stored artefacts.

``raw/{YYYY}/{MM}/{YYYY-MM-DD}T{HHMM}Z_{short}.{ext}``

Two properties are doing the work. The name sorts chronologically in any file
manager, and it carries enough of the hash to be matched back to the event log
by eye. Someone opening this folder in five years with nothing installed can
still tell what they are looking at and in what order it arrived — which is the
whole point of the naming rule.

The timestamp is **ingest** time, the moment the bytes landed in the vault. It
is the only timestamp always known at this point, and it is deliberately not
presented as capture time anywhere: a photo dragged in three days after it was
taken has an ingest time three days after its capture time, and writing one into
the other is the silent timeline corruption ``CLAUDE.md`` forbids.

``CLAUDE.md`` calls the timestamp "ISO8601 basic"; both worked examples in the
spec are extended date with basic time at minute precision
(``2026-09-08T1432Z``), and the examples are what is implemented here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Callable

from ..errors import IngestError
from ..events import envelope
from .hashing import HASH_LENGTH

RAW_DIRNAME = "raw"

#: How much of the digest goes in the filename. Six hex characters is 24 bits,
#: so two unrelated artefacts share a prefix at around four thousand files —
#: entirely reachable for a lifetime record. The prefix is therefore an
#: affordance for humans, never an identifier: :func:`allocate` lengthens it on
#: collision and the full digest is what the event log keys on.
SHORT_HASH_CHARS = 6

TS_FORMAT = "%Y-%m-%dT%H%MZ"

_NAME_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{4}Z)"
    r"_(?P<short>[0-9a-f]{%d,%d})"
    r"\.(?P<ext>[a-z0-9]{1,8})$" % (SHORT_HASH_CHARS, HASH_LENGTH)
)

_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")

#: Suffix for a file being written but not yet complete. Never a valid artefact
#: name, so a leftover is reported rather than mistaken for one.
PARTIAL_SUFFIX = ".partial"


@dataclass(frozen=True)
class ParsedName:
    """A stored artefact filename, taken apart."""

    ts: str  # as written in the filename, minute precision
    short: str
    ext: str

    @property
    def stem(self) -> str:
        return f"{self.ts}_{self.short}"

    @property
    def name(self) -> str:
        return f"{self.stem}.{self.ext}"


def _instant(ts: str) -> datetime:
    parsed = envelope.parse_ts_or_none(ts)
    if parsed is None:
        raise IngestError(f"cannot build a filename from an unparseable timestamp {ts!r}")
    return parsed


def filename_ts(ts: str) -> str:
    """Render a canonical event timestamp in the filename's minute precision."""
    return _instant(ts).strftime(TS_FORMAT)


def month_dir(ts: str) -> PurePosixPath:
    """The vault-relative directory an artefact ingested at *ts* belongs in."""
    instant = _instant(ts)
    return PurePosixPath(RAW_DIRNAME) / instant.strftime("%Y") / instant.strftime("%m")


def is_valid_extension(ext: str) -> bool:
    return bool(_EXT_RE.match(ext))


def build(ts: str, short: str, ext: str) -> str:
    """Assemble one filename. No collision handling — see :func:`allocate`."""
    if not is_valid_extension(ext):
        raise IngestError(f"{ext!r} is not a usable file extension")
    return f"{filename_ts(ts)}_{short}.{ext}"


def allocate(ts: str, digest: str, ext: str, taken: Callable[[str], bool]) -> str:
    """Choose a filename that is not already in use.

    *taken* is asked about the **stem**, not the full name, so an artefact never
    collides with a differently-typed neighbour or with its own sidecar. On a
    clash the hash prefix lengthens by one character at a time and is retried:
    the result stays derived from the content rather than from a counter, so it
    does not depend on the order files happened to arrive.
    """
    stamp = filename_ts(ts)
    for length in range(SHORT_HASH_CHARS, HASH_LENGTH + 1):
        stem = f"{stamp}_{digest[:length]}"
        if not taken(stem):
            return build(ts, digest[:length], ext)
    raise IngestError(
        f"an artefact with the full digest {digest} is already stored at {stamp}; "
        f"identical content should have been deduplicated rather than renamed"
    )


def parse(name: str) -> ParsedName | None:
    """Take apart a stored artefact filename, or return ``None``.

    The grammar is the membership test for the raw store: anything that fails it
    is reported as a foreign file rather than silently swept into the record.
    """
    match = _NAME_RE.match(name)
    if not match:
        return None
    try:
        datetime.strptime(match.group("ts"), TS_FORMAT)
    except ValueError:
        return None
    return ParsedName(ts=match.group("ts"), short=match.group("short"), ext=match.group("ext"))


def is_partial(name: str) -> bool:
    return name.endswith(PARTIAL_SUFFIX)
