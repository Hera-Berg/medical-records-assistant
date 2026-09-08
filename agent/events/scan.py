"""What a synced folder does to files, and how to report it honestly.

Three things happen to a vault that lives inside Dropbox, Drive or Nextcloud,
and none of them are corruption:

* the client forks a file into a "conflicted copy" rather than merging it;
* a virtual-drive file exists as a placeholder that has not been downloaded;
* a crash leaves the final line of a shard without its newline.

Each gets its own reported state. In particular a placeholder is never reported
as a malformed line: "not yet downloaded" and "the contents are wrong" call for
completely different responses from the user, and collapsing them means someone
concludes their record is damaged when it is merely offline.
"""

from __future__ import annotations

import errno
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..config import _DROPBOX_CONFLICT, _NEXTCLOUD_CONFLICT, _NUMBERED_COPY, SyncProfile

#: Sidecar names some Nextcloud/ownCloud clients leave beside a virtual file.
_PLACEHOLDER_SUFFIXES = (".nextcloud", ".owncloud")

#: Errno values a virtual drive raises when it cannot hydrate a file.
_OFFLINE_ERRNOS = frozenset({errno.EIO, errno.ENODATA, errno.ENOENT, errno.EACCES})

#: label -> (pattern, events_only). The numbered-copy pattern is generic enough
#: to hit innocent filenames such as "scan (1).jpg", so it is applied only inside
#: events/, where a fork means silent event loss and is worth the noise.
_PATTERN_LABELS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    ("dropbox conflicted copy", _DROPBOX_CONFLICT, False),
    ("nextcloud conflict copy", _NEXTCLOUD_CONFLICT, False),
    ("numbered copy", _NUMBERED_COPY, True),
)


class ShardState(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    PLACEHOLDER = "placeholder"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class ConflictFile:
    """A file whose name says a sync client forked it."""

    path: Path
    patterns: tuple[str, ...]

    def describe(self, root: Path) -> str:
        rel = self.path.relative_to(root)
        return (
            f"{rel} looks like a sync fork ({', '.join(self.patterns)}). Its events "
            f"are not in the merged view. Merge it by hand into the matching shard "
            f"and delete it — do not simply ignore it."
        )


@dataclass(frozen=True)
class ShardContent:
    """The bytes of a shard, plus what the filesystem said about getting them."""

    state: ShardState
    data: bytes = b""
    detail: str = ""


def placeholder_reason(path: Path) -> str | None:
    """Cheaply decide whether *path* is a virtual file that is not downloaded.

    Confirmed by probing one byte rather than by inspecting ``st_blocks``:
    filesystems that inline small files report zero blocks for perfectly
    ordinary data, and wrongly calling a materialised shard a placeholder would
    hide real events.
    """
    for suffix in _PLACEHOLDER_SUFFIXES:
        if path.with_name(path.name + suffix).exists():
            return f"virtual file, not downloaded ({path.name}{suffix} present)"

    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None

    try:
        with open(path, "rb") as handle:
            head = handle.read(1)
    except OSError as exc:
        if exc.errno in _OFFLINE_ERRNOS:
            return f"could not be materialised: {exc.strerror or exc}"
        return None
    if not head:
        return f"reports {size} bytes but yields none \u2014 not yet downloaded"
    return None


def read_shard_bytes(path: Path, profile: SyncProfile) -> ShardContent:
    """Read a shard, classifying the ways it can fail to yield bytes."""
    reason = placeholder_reason(path)
    if reason is not None:
        return ShardContent(ShardState.PLACEHOLDER, detail=reason)

    try:
        data = path.read_bytes()
    except OSError as exc:
        if exc.errno in _OFFLINE_ERRNOS:
            return ShardContent(
                ShardState.PLACEHOLDER,
                detail=f"could not be materialised: {exc.strerror or exc}",
            )
        return ShardContent(ShardState.UNREADABLE, detail=f"cannot read: {exc}")

    if not data:
        detail = (
            "empty \u2014 not yet downloaded, or written but never appended to"
            if profile.may_have_placeholders
            else "empty"
        )
        return ShardContent(ShardState.EMPTY, detail=detail)

    return ShardContent(ShardState.OK, data=data)


def find_conflicts(root: Path, profile: SyncProfile) -> list[ConflictFile]:
    """Find sync-fork files anywhere in the vault.

    Surfaced for merge, never merged automatically and never ignored.
    """
    active = profile.conflict_patterns
    found: list[ConflictFile] = []
    for path in sorted(root.rglob("*")):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if path.name.endswith(_PLACEHOLDER_SUFFIXES):
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        in_events = bool(rel.parts) and rel.parts[0] == "events"
        labels = [
            label
            for label, pattern, events_only in _PATTERN_LABELS
            if pattern in active
            and (in_events or not events_only)
            and pattern.search(path.name)
        ]
        if labels:
            found.append(ConflictFile(path=path, patterns=tuple(labels)))
    return found
