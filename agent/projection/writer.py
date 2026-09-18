"""Writing the derived files, without ever deleting what we did not write.

The wiki is derived and the vault is the user's folder. Those two facts pull in
opposite directions, and the manifest is what resolves them: it records exactly
which files this code wrote last time, and the next run may remove **only**
those. Anything else in ``wiki/`` — a note the user dropped in by hand, a sync
client's conflicted copy of a generated page — is foreign. It is reported and
left alone.

A file the manifest lists but whose bytes have changed since is also left alone.
Someone edited a generated page; that edit is theirs, and losing it silently to a
rebuild would be worse than the stale file it leaves behind.

If the manifest is missing entirely, nothing is deleted at all. An absent
manifest is not evidence of an empty folder, and treating it as one is how a
rebuild after a restore takes the user's own files with it.

Every byte goes out in binary with ``\\n`` endings — never text mode, which
would rewrite every ending to CRLF on Windows and break byte-identical rebuild
between machines for reasons invisible in the log.
"""

from __future__ import annotations
from ..files import O_BINARY

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..events import canonical
from ..ingest.hashing import hash_file
from .subjects import WIKI_DIRNAME

MANIFEST_REL = ".agent/projection/manifest.json"
MANIFEST_VERSION = 1

#: Litter that file managers and sync clients drop into any folder. Ignored
#: rather than reported, exactly as the raw store treats them.
_IGNORED_NAMES = frozenset(
    {".DS_Store", "Thumbs.db", "desktop.ini", ".localized", ".directory"}
)


def _is_litter(name: str) -> bool:
    return name in _IGNORED_NAMES or name.endswith((".nextcloud", ".owncloud"))


@dataclass(frozen=True)
class WriteReport:
    """What the write did, and what it deliberately did not do."""

    written: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    #: Files in wiki/ this code did not write. Never touched.
    foreign: tuple[str, ...] = ()
    #: Files the manifest lists but which have been edited since. Never touched.
    modified: tuple[str, ...] = ()
    manifest_missing: bool = False

    @property
    def problems(self) -> tuple[str, ...]:
        notes: list[str] = []
        if self.manifest_missing and (self.foreign or self.modified):
            notes.append(
                f"{MANIFEST_REL} is missing, so nothing was removed. "
                f"{len(self.foreign) + len(self.modified)} file(s) in {WIKI_DIRNAME}/ are "
                f"unaccounted for and were left in place."
            )
        for rel in self.foreign:
            notes.append(f"{rel} is in {WIKI_DIRNAME}/ but was not generated; left alone")
        for rel in self.modified:
            notes.append(
                f"{rel} was generated but has been edited since; left alone rather than "
                f"overwritten. The wiki is regenerated wholesale, so an edit here will "
                f"not survive as a source — record it as a note or a correction instead."
            )
        return tuple(notes)


@dataclass
class Manifest:
    """The record of what the last write produced."""

    as_of: str | None = None
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    present: bool = False

    def to_json(self) -> bytes:
        return (
            canonical.dumps(
                {
                    "version": MANIFEST_VERSION,
                    "as_of": self.as_of,
                    "stats": self.stats,
                    "files": self.files,
                }
            )
            + "\n"
        ).encode("utf-8")


def read_manifest(root: Path) -> Manifest:
    path = root / MANIFEST_REL
    if not path.exists():
        return Manifest()
    try:
        data = canonical.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # An unreadable manifest is treated exactly like a missing one: it
        # authorises no deletions.
        return Manifest()
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return Manifest()
    return Manifest(
        as_of=data.get("as_of"),
        files={k: v for k, v in data["files"].items() if isinstance(v, dict)},
        stats=data.get("stats") if isinstance(data.get("stats"), dict) else {},
        present=True,
    )


def _existing_files(root: Path) -> dict[str, Path]:
    wiki = root / WIKI_DIRNAME
    found: dict[str, Path] = {}
    if not wiki.is_dir():
        return found
    for path in sorted(wiki.rglob("*")):
        if not path.is_file() or _is_litter(path.name):
            continue
        found[path.relative_to(root).as_posix()] = path
    return found


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace *path* with *data*, in binary, or leave it untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_BINARY, 0o600)
    try:
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)


def write(
    root: Path,
    files: Mapping[str, bytes],
    as_of: str,
    stats: Mapping[str, Any] | None = None,
) -> WriteReport:
    """Write the derived files, remove only what the manifest accounts for."""
    previous = read_manifest(root)
    existing = _existing_files(root)

    written: list[str] = []
    unchanged: list[str] = []
    removed: list[str] = []
    foreign: list[str] = []
    modified: list[str] = []

    new_manifest: dict[str, dict[str, Any]] = {}

    for rel in sorted(files):
        data = files[rel]
        target = root / rel
        digest = _digest(data)
        if target.exists():
            try:
                current, _ = hash_file(target)
            except OSError:
                current = None
            if current == digest:
                unchanged.append(rel)
                new_manifest[rel] = {"sha256": digest, "bytes": len(data)}
                continue
            recorded = previous.files.get(rel, {}).get("sha256")
            if previous.present and recorded is not None and current != recorded:
                # Generated, then edited by hand. Not ours to overwrite.
                modified.append(rel)
                new_manifest[rel] = {"sha256": recorded, "bytes": previous.files[rel].get("bytes", 0)}
                continue
            if not previous.present and current is not None:
                foreign.append(rel)
                continue
        _write_atomic(target, data)
        written.append(rel)
        new_manifest[rel] = {"sha256": digest, "bytes": len(data)}

    for rel in sorted(existing):
        if rel in files:
            continue
        recorded = previous.files.get(rel)
        if recorded is None:
            foreign.append(rel)
            continue
        try:
            current, _ = hash_file(existing[rel])
        except OSError:
            current = None
        if current != recorded.get("sha256"):
            modified.append(rel)
            continue
        existing[rel].unlink(missing_ok=True)
        removed.append(rel)

    _prune_empty_dirs(root / WIKI_DIRNAME)

    manifest = Manifest(as_of=as_of, files=new_manifest, stats=dict(stats or {}))
    _write_atomic(root / MANIFEST_REL, manifest.to_json())

    return WriteReport(
        written=tuple(written),
        unchanged=tuple(unchanged),
        removed=tuple(removed),
        foreign=tuple(sorted(set(foreign))),
        modified=tuple(sorted(set(modified))),
        manifest_missing=not previous.present,
    )


def _digest(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _prune_empty_dirs(top: Path) -> None:
    """Remove directories this code emptied. Never removes a non-empty one."""
    if not top.is_dir():
        return
    for path in sorted(top.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            try:
                path.rmdir()
            except OSError:
                pass
