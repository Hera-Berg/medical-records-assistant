"""Append-only JSONL shards, and the deterministic union read over them.

One file per device per month: ``events/{YYYY-MM}.{device-id}.jsonl``. Two
devices appending to one file on Dropbox produces "conflicted copy" files and
silent loss; separate files unioned and sorted at read time never conflict.

Lines are never rewritten and never deleted. Corrections are new events. The
only write this module performs is an append of complete, newline-terminated
bytes to the end of a shard.
"""

from __future__ import annotations
from ..files import O_BINARY

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import SyncProfile
from ..errors import AppendError, EventValidationError
from . import canonical, envelope, ulid
from .envelope import Event
from .scan import ShardState, placeholder_reason, read_shard_bytes

SHARD_SUFFIX = ".jsonl"
_SHARD_RE = re.compile(r"^(?P<month>\d{4}-\d{2})\.(?P<device>[a-z0-9][a-z0-9-]{0,23})\.jsonl$")

#: How much of a bad line to quote back. Enough to recognise it, not enough to
#: dump a whole extraction into a terminal.
_EXCERPT = 200


@dataclass(frozen=True)
class MalformedLine:
    """A line that could not be read as an event. Reported, never dropped."""

    path: Path
    lineno: int
    reason: str
    excerpt: str

    def describe(self) -> str:
        return f"{self.path.name}:{self.lineno}: {self.reason} — {self.excerpt!r}"


@dataclass(frozen=True)
class ShardReport:
    """What one shard file turned out to be."""

    path: Path
    state: ShardState
    detail: str = ""
    month: str | None = None
    device: str | None = None
    event_count: int = 0
    byte_size: int = 0
    torn_final_line: bool = False
    malformed: tuple[MalformedLine, ...] = ()
    anomalies: tuple[str, ...] = ()
    first_ts: str | None = None
    last_ts: str | None = None

    @property
    def is_clean(self) -> bool:
        return (
            self.state in (ShardState.OK, ShardState.EMPTY)
            and not self.malformed
            and not self.anomalies
        )


@dataclass(frozen=True)
class LogRead:
    """The merged log, plus everything that was wrong with getting it."""

    events: tuple[Event, ...] = ()
    shards: tuple[ShardReport, ...] = ()
    foreign_files: tuple[Path, ...] = ()
    duplicate_ids: tuple[tuple[str, tuple[Path, ...]], ...] = ()

    @property
    def malformed(self) -> tuple[MalformedLine, ...]:
        return tuple(m for shard in self.shards for m in shard.malformed)

    @property
    def anomalies(self) -> tuple[str, ...]:
        return tuple(a for shard in self.shards for a in shard.anomalies)

    @property
    def unavailable_shards(self) -> tuple[ShardReport, ...]:
        """Shards whose contents this machine does not currently have."""
        return tuple(
            s for s in self.shards
            if s.state in (ShardState.PLACEHOLDER, ShardState.UNREADABLE)
        )

    @property
    def is_clean(self) -> bool:
        return (
            all(s.is_clean for s in self.shards)
            and not self.foreign_files
            and not self.duplicate_ids
        )


def shard_name(month: str, device: str) -> str:
    return f"{month}.{device}{SHARD_SUFFIX}"


def parse_shard_name(name: str) -> tuple[str, str] | None:
    """Return ``(month, device)`` if *name* is a well-formed shard filename.

    The grammar is the union read's membership test. A conflicted copy
    (``2026-09.laptop (conflicted copy 2026-09-08).jsonl``) or a numbered fork
    (``2026-09.laptop(1).jsonl``) fails it, which is how such files stay out of
    the merged view regardless of which sync profile is configured. They are
    reported as foreign files rather than silently skipped.
    """
    match = _SHARD_RE.match(name)
    if not match:
        return None
    month = match.group("month")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        return None
    return month, match.group("device")


def month_of(ts: str) -> str:
    """The shard month for a canonical timestamp, in UTC."""
    instant = envelope.parse_ts_or_none(ts)
    if instant is None:
        raise EventValidationError(f"cannot derive a shard month from ts {ts!r}")
    return instant.strftime("%Y-%m")


def shard_path(events_dir: Path, device: str, ts: str) -> Path:
    return events_dir / shard_name(month_of(ts), device)


def _ends_without_newline(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                return False
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"
    except OSError:
        return False


def append(events_dir: Path, event: Event, profile: SyncProfile) -> Path:
    """Append one validated event to its device/month shard.

    Returns the shard path. Raises rather than reporting a partial success:
    an event the caller believes was recorded but which is not on disk is worse
    than a visible failure.
    """
    envelope.validate_for_append(event)
    events_dir.mkdir(parents=True, exist_ok=True)
    path = shard_path(events_dir, event.device, event.ts)

    if path.exists():
        reason = placeholder_reason(path)
        if reason is not None:
            # Appending to a file whose earlier contents have not been
            # downloaded risks the client resolving the write as a fork, or
            # materialising a file that contains only the new line.
            raise AppendError(
                f"{path.name} has not been downloaded yet ({reason}). Refusing to "
                f"append: the events already in it would be at risk. Wait for the "
                f"sync client to materialise the file, or mark it available offline."
            )

    line = canonical.dump_line(event.to_dict())
    # A previous process died mid-write. Never rewrite the torn bytes — lead
    # with a newline so they survive as their own (malformed, reported) line and
    # this event still lands.
    torn = _ends_without_newline(path)
    data = (b"\n" + line) if torn else line

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | O_BINARY, 0o600)
    try:
        # Where this append starts, so the readback knows what to look at.
        offset = os.lseek(fd, 0, os.SEEK_END)
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
        os.fsync(fd)
    finally:
        os.close(fd)

    if profile.verify_readback:
        _verify_readback(path, data, offset)
    return path


def _verify_readback(path: Path, expected: bytes, offset: int) -> None:
    """Confirm the bytes actually landed.

    Virtual drives report success from ``fsync`` on writes the client has not
    committed. Cheap insurance on the one operation whose failure is invisible.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            got = handle.read(len(expected))
    except OSError as exc:
        raise AppendError(
            f"appended to {path.name} but could not read the line back: {exc}. "
            f"Treat the event as not recorded."
        ) from exc
    if got != expected:
        raise AppendError(
            f"appended to {path.name} but the line read back differently. The sync "
            f"client may not have committed the write. Treat the event as not recorded."
        )


def _excerpt(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    return text if len(text) <= _EXCERPT else text[:_EXCERPT] + "…"


def read_shard(path: Path, profile: SyncProfile) -> tuple[ShardReport, list[Event]]:
    """Read one shard. Never raises for bad content; reports it instead."""
    parsed_name = parse_shard_name(path.name)
    month = parsed_name[0] if parsed_name else None
    device = parsed_name[1] if parsed_name else None

    content = read_shard_bytes(path, profile)
    try:
        byte_size = path.stat().st_size
    except OSError:
        byte_size = 0

    if content.state is not ShardState.OK:
        return (
            ShardReport(
                path=path,
                state=content.state,
                detail=content.detail,
                month=month,
                device=device,
                byte_size=byte_size,
            ),
            [],
        )

    events: list[Event] = []
    malformed: list[MalformedLine] = []
    anomalies: list[str] = []

    raw_lines = content.data.split(b"\n")
    torn_final_line = raw_lines[-1] != b""
    if not torn_final_line:
        raw_lines = raw_lines[:-1]

    for lineno, raw in enumerate(raw_lines, start=1):
        raw = raw.rstrip(b"\r")
        if not raw.strip():
            continue  # blank separator; carries nothing, loses nothing
        is_torn_tail = torn_final_line and lineno == len(raw_lines)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            malformed.append(MalformedLine(path, lineno, f"not valid UTF-8: {exc}", _excerpt(raw)))
            continue
        try:
            obj = canonical.loads(text)
        except json.JSONDecodeError as exc:
            reason = (
                "final line has no terminating newline — an interrupted write"
                if is_torn_tail
                else f"not valid JSON: {exc.msg} at column {exc.colno}"
            )
            malformed.append(MalformedLine(path, lineno, reason, _excerpt(raw)))
            continue
        try:
            event = envelope.from_dict(obj)
        except EventValidationError as exc:
            malformed.append(MalformedLine(path, lineno, str(exc), _excerpt(raw)))
            continue

        if is_torn_tail:
            anomalies.append(
                f"{path.name}:{lineno}: last line has no terminating newline; it parsed, "
                f"but a write was interrupted here"
            )
        if event.type not in envelope.EVENT_TYPES:
            anomalies.append(
                f"{path.name}:{lineno}: unknown event type {event.type!r}, kept as-is"
            )
        if not ulid.is_valid(event.id):
            anomalies.append(f"{path.name}:{lineno}: id {event.id!r} is not a valid ULID")
        if envelope.parse_ts_or_none(event.ts) is None:
            anomalies.append(f"{path.name}:{lineno}: ts {event.ts!r} is unparseable")
        if device is not None and event.device != device:
            anomalies.append(
                f"{path.name}:{lineno}: event device {event.device!r} does not match the "
                f"shard's device {device!r}"
            )
        if month is not None:
            instant = envelope.parse_ts_or_none(event.ts)
            if instant is not None and instant.strftime("%Y-%m") != month:
                anomalies.append(
                    f"{path.name}:{lineno}: ts {event.ts} does not fall in the shard's "
                    f"month {month}"
                )
        events.append(event)

    timestamps = sorted(e.ts for e in events)
    report = ShardReport(
        path=path,
        state=ShardState.OK,
        detail=content.detail,
        month=month,
        device=device,
        event_count=len(events),
        byte_size=byte_size,
        torn_final_line=torn_final_line,
        malformed=tuple(malformed),
        anomalies=tuple(anomalies),
        first_ts=timestamps[0] if timestamps else None,
        last_ts=timestamps[-1] if timestamps else None,
    )
    return report, events


def read_all(events_dir: Path, profile: SyncProfile) -> LogRead:
    """Union every shard into one deterministically ordered event stream.

    The order is ``(ts, id)`` and depends on nothing else — not on directory
    listing order, not on which device wrote what, not on the order shards were
    read. That is what makes replay reproducible across machines.
    """
    if not events_dir.exists():
        return LogRead()

    shards: list[ShardReport] = []
    foreign: list[Path] = []
    events: list[Event] = []
    origins: dict[str, list[Path]] = {}

    for path in sorted(events_dir.iterdir(), key=lambda p: p.name):
        if path.is_dir():
            continue
        if path.name.endswith((".nextcloud", ".owncloud")):
            continue
        if parse_shard_name(path.name) is None:
            foreign.append(path)
            continue
        report, shard_events = read_shard(path, profile)
        shards.append(report)
        for event in shard_events:
            origins.setdefault(event.id, []).append(path)
            events.append(event)

    events.sort(key=lambda e: e.sort_key)

    duplicates = tuple(
        (event_id, tuple(paths))
        for event_id, paths in sorted(origins.items())
        if len(paths) > 1
    )

    return LogRead(
        events=tuple(events),
        shards=tuple(shards),
        foreign_files=tuple(foreign),
        duplicate_ids=duplicates,
    )
