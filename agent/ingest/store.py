"""``raw/`` — the original files, and everything that can go wrong with them.

The raw store holds artefacts exactly as they arrived, laid out by ingest month.
It is not derived: the rebuild in phase 3 wipes ``wiki/`` and ``.agent/`` and
replays ``raw/`` plus ``events/``, so nothing here can be regenerated and
nothing here is ever removed by this code.

Stored artefacts are given mode ``0o400``. That is a guardrail against an
accidental overwrite by a script or a text editor, and nothing more — the
directory still permits deletion, the owner can change it back, and no claim
stronger than "accidents fail loudly" should ever be made for it. Integrity
checks here therefore ignore permission bits entirely: a restore from backup or
a resync loses modes routinely, and reporting that as damage would train the
user to ignore the reports that matter.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, Mapping

from ..config import SyncProfile
from ..errors import IngestError
from ..events import ulid
from ..events.envelope import Event
from ..events.scan import _PLACEHOLDER_SUFFIXES, placeholder_reason
from . import naming, sidecar
from .hashing import copy_and_hash, hash_file, is_valid_hash

#: Read-only as an anti-accident guardrail. Never described as immutability:
#: the containing directory is still writable and the mode is still the owner's
#: to change.
ARTIFACT_MODE = 0o400

_STAGING_PREFIX = ".ingest-"

#: Litter that file managers and sync clients drop into any folder they touch.
#: Reporting these as foreign files every run would train the user to skip the
#: report, so they are ignored rather than surfaced.
_IGNORED_NAMES = frozenset(
    {".DS_Store", "Thumbs.db", "desktop.ini", ".localized", ".directory"}
)


def _is_litter(name: str) -> bool:
    return name in _IGNORED_NAMES or name.endswith(_PLACEHOLDER_SUFFIXES)


@dataclass(frozen=True)
class Staged:
    """Bytes written to a temporary file, hashed, not yet part of the record."""

    path: Path
    digest: str
    size: int
    head: bytes


@dataclass(frozen=True)
class StoredArtifact:
    """One artefact on disk, and what its filename says about it."""

    path: Path
    rel: str
    parsed: naming.ParsedName
    size: int

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def sidecar_path(self) -> Path:
        return sidecar.path_for(self.path)


@dataclass(frozen=True)
class IngestedRecord:
    """What the event log says about one artefact."""

    digest: str
    rel: str
    size: int
    mime: str
    event_id: str
    ingested_ts: str


@dataclass(frozen=True)
class RawStoreReport:
    """The state of ``raw/``, assembled for ``health-agent check``."""

    artifacts: int = 0
    bytes: int = 0
    deep: bool = False
    orphans: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    sidecar_missing: tuple[str, ...] = ()
    sidecar_disagrees: tuple[str, ...] = ()
    sidecar_orphaned: tuple[str, ...] = ()
    hash_mismatch: tuple[str, ...] = ()
    foreign: tuple[str, ...] = ()
    partials: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()
    duplicate_events: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not (
            self.orphans
            or self.missing
            or self.sidecar_missing
            or self.sidecar_disagrees
            or self.sidecar_orphaned
            or self.hash_mismatch
            or self.foreign
            or self.partials
            or self.duplicate_events
        )


def ingested_records(events: Iterable[Event]) -> tuple[dict[str, IngestedRecord], tuple[str, ...]]:
    """Index ``artifact.ingested`` events by digest.

    A linear pass over the log. The SQLite index that would make this a lookup
    is a disposable cache and does not exist until a later phase; the log is the
    only thing entitled to answer "have we seen these bytes before" anyway.
    """
    records: dict[str, IngestedRecord] = {}
    duplicates: list[str] = []
    for event in events:
        if event.type != "artifact.ingested":
            continue
        payload = event.payload
        digest = payload.get("hash")
        if not is_valid_hash(digest):
            continue
        if digest in records:
            duplicates.append(
                f"{digest[:12]}… was ingested twice ({records[digest].event_id} and "
                f"{event.id}); the second should have been an artifact.reseen"
            )
            continue
        records[digest] = IngestedRecord(
            digest=digest,
            rel=str(payload.get("path") or ""),
            size=int(payload.get("bytes") or 0),
            mime=str(payload.get("mime") or ""),
            event_id=event.id,
            ingested_ts=str(payload.get("ingested_ts") or event.ts),
        )
    return records, tuple(duplicates)


class RawStore:
    """The ``raw/`` tree of one vault."""

    def __init__(self, root: Path, profile: SyncProfile = SyncProfile.LOCAL):
        self.root = root
        self.profile = profile

    @property
    def raw_dir(self) -> Path:
        return self.root / naming.RAW_DIRNAME

    def month_dir(self, ingested_ts: str) -> Path:
        return self.root / naming.month_dir(ingested_ts)

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def resolve_recorded(self, rel: str) -> Path | None:
        """Turn a path recorded in the log into an absolute one, or refuse.

        Event payloads are ordinary lines in a text file that a person can
        edit, and a restore writes bytes to wherever this points. Anything
        absolute, or that climbs out of the vault, is refused rather than
        followed — the vault is the only place this code writes.
        """
        if not rel:
            return None
        candidate = Path(rel)
        if candidate.is_absolute():
            return None
        target = (self.root / candidate).resolve()
        root = self.root.resolve()
        if target != root and root not in target.parents:
            return None
        return target

    # --- writing ------------------------------------------------------------

    def stage(self, source: BinaryIO, ingested_ts: str) -> Staged:
        """Stream *source* into a temporary file in its destination directory.

        Staging in the final directory rather than in a system temp dir keeps
        the later move within one filesystem, so it is a rename and not a
        second copy of a large scan.
        """
        month = self.month_dir(ingested_ts)
        month.mkdir(parents=True, exist_ok=True)
        temp = month / f"{_STAGING_PREFIX}{ulid.new()}{naming.PARTIAL_SUFFIX}"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                digest, size, head = copy_and_hash(source, handle)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        return Staged(path=temp, digest=digest, size=size, head=head)

    def discard(self, staged: Staged) -> None:
        """Drop staged bytes that turned out to be a duplicate."""
        staged.path.unlink(missing_ok=True)

    def commit(self, staged: Staged, ingested_ts: str, ext: str) -> Path:
        """Move staged bytes to their allocated name under ``raw/``."""
        month = self.month_dir(ingested_ts)
        name = naming.allocate(
            ingested_ts,
            staged.digest,
            ext,
            taken=lambda stem: any(month.glob(f"{stem}.*")),
        )
        return self._place(staged, month / name)

    def commit_to(self, staged: Staged, target: Path) -> Path:
        """Move staged bytes to an exact path.

        Used when the log already records where these bytes belong and the file
        itself has gone missing, so that the citation in the wiki keeps pointing
        at the same relative path it always did.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        return self._place(staged, target)

    def _place(self, staged: Staged, target: Path) -> Path:
        """Put staged bytes at *target* without ever overwriting what is there."""
        try:
            os.link(staged.path, target)
        except FileExistsError:
            raise IngestError(
                f"{self.relative(target)} already exists; refusing to overwrite an "
                f"artefact that is already in the record"
            ) from None
        except OSError:
            # Filesystems without hard links (some network and virtual drives).
            if target.exists():
                raise IngestError(
                    f"{self.relative(target)} already exists; refusing to overwrite an "
                    f"artefact that is already in the record"
                ) from None
            os.replace(staged.path, target)
        else:
            staged.path.unlink(missing_ok=True)

        try:
            os.chmod(target, ARTIFACT_MODE)
        except OSError:
            pass  # A mode we cannot set is not a reason to fail an ingest.
        self._fsync_dir(target.parent)
        return target

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return  # Windows, and anywhere else a directory cannot be opened.
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def verify_stored(self, path: Path, digest: str) -> None:
        """Re-read a just-written artefact and confirm the bytes match.

        Only on synced profiles, mirroring the event log's readback: a virtual
        drive can report a successful write for a file it has not committed.
        The file is left in place on failure — it is reported as an orphan by
        ``check`` rather than deleted, because deleting raw bytes is not
        something this code does.
        """
        if not self.profile.verify_readback:
            return
        try:
            stored, _ = hash_file(path)
        except OSError as exc:
            raise IngestError(
                f"wrote {self.relative(path)} but could not read it back: {exc}. "
                f"Nothing was recorded. The file is on disk, and adding the same "
                f"file again records it."
            ) from exc
        if stored != digest:
            raise IngestError(
                f"wrote {self.relative(path)} but it read back with a different hash. "
                f"The sync client may not have committed the write. Nothing was "
                f"recorded. The file is on disk, and adding the same file again "
                f"records it once the sync client has caught up."
            )

    # --- reading ------------------------------------------------------------

    def iter_artifacts(self) -> Iterator[StoredArtifact]:
        """Every well-named artefact under ``raw/``, in path order."""
        if not self.raw_dir.is_dir():
            return
        for path in sorted(self.raw_dir.rglob("*")):
            if not path.is_file() or _is_litter(path.name):
                continue
            parsed = naming.parse(path.name)
            if parsed is None:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            yield StoredArtifact(
                path=path, rel=self.relative(path), parsed=parsed, size=size
            )

    def find(self, digest: str) -> Path | None:
        """Locate stored bytes by full digest.

        Matches the filename prefix first, which is cheap, then confirms against
        the sidecar so that a shared prefix between two unrelated artefacts
        cannot return the wrong file.
        """
        if not is_valid_hash(digest):
            return None
        for artifact in self.iter_artifacts():
            if not digest.startswith(artifact.parsed.short):
                continue
            data = sidecar.read(artifact.sidecar_path)
            if data is not None:
                if data.get("hash") == digest:
                    return artifact.path
                continue
            try:
                stored, _ = hash_file(artifact.path)
            except OSError:
                continue
            if stored == digest:
                return artifact.path
        return None

    def verify(
        self,
        records: Mapping[str, IngestedRecord],
        deep: bool = False,
        duplicates: tuple[str, ...] = (),
    ) -> RawStoreReport:
        """Compare the files on disk against what the log says should be there.

        Reports; never repairs. ``deep`` re-hashes every artefact, which is a
        full read of the whole store and therefore opt-in.
        """
        orphans: list[str] = []
        missing: list[str] = []
        sidecar_missing: list[str] = []
        sidecar_disagrees: list[str] = []
        sidecar_orphaned: list[str] = []
        hash_mismatch: list[str] = []
        foreign: list[str] = []
        partials: list[str] = []
        unavailable: list[str] = []

        by_rel = {record.rel: record for record in records.values()}
        seen_rel: set[str] = set()
        count = 0
        total_bytes = 0

        if self.raw_dir.is_dir():
            for path in sorted(self.raw_dir.rglob("*")):
                if not path.is_file():
                    continue
                name = path.name
                if _is_litter(name):
                    continue
                rel = self.relative(path)
                if naming.is_partial(name):
                    partials.append(rel)
                    continue
                described = sidecar.artifact_name_of(name)
                if described is not None and naming.parse(described) is not None:
                    if not path.with_name(described).exists():
                        sidecar_orphaned.append(rel)
                    continue

                parsed = naming.parse(name)
                if parsed is None:
                    foreign.append(rel)
                    continue

                count += 1
                seen_rel.add(rel)
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    pass

                offline = placeholder_reason(path)
                if offline is not None:
                    unavailable.append(f"{rel}: {offline}")
                    continue

                data = sidecar.read(sidecar.path_for(path))
                if data is None:
                    sidecar_missing.append(rel)
                    digest = None
                else:
                    digest = data.get("hash")

                if deep:
                    try:
                        actual, _ = hash_file(path)
                    except OSError as exc:
                        unavailable.append(f"{rel}: cannot read: {exc}")
                        continue
                    if data is not None:
                        problem = sidecar.disagreement(data, name, actual)
                        if problem:
                            sidecar_disagrees.append(f"{rel}: {problem}")
                    recorded_here = by_rel.get(rel)
                    if recorded_here is not None:
                        if recorded_here.digest != actual:
                            hash_mismatch.append(
                                f"{rel} no longer holds the bytes the log records for "
                                f"it (event {recorded_here.event_id}); the file has "
                                f"been modified or has decayed since it was ingested"
                            )
                    elif actual in records:
                        hash_mismatch.append(
                            f"{rel} holds the bytes the log records at "
                            f"{records[actual].rel}"
                        )
                    else:
                        orphans.append(rel)
                    continue

                if data is not None:
                    problem = sidecar.disagreement(data, name)
                    if problem:
                        sidecar_disagrees.append(f"{rel}: {problem}")
                if rel not in by_rel and (digest is None or digest not in records):
                    orphans.append(rel)

        for record in records.values():
            if not record.rel:
                continue
            if not (self.root / record.rel).exists():
                missing.append(
                    f"{record.rel} (hash {record.digest[:12]}…, event {record.event_id})"
                )

        return RawStoreReport(
            artifacts=count,
            bytes=total_bytes,
            deep=deep,
            orphans=tuple(orphans),
            missing=tuple(missing),
            sidecar_missing=tuple(sidecar_missing),
            sidecar_disagrees=tuple(sidecar_disagrees),
            sidecar_orphaned=tuple(sidecar_orphaned),
            hash_mismatch=tuple(hash_mismatch),
            foreign=tuple(foreign),
            partials=tuple(partials),
            unavailable=tuple(unavailable),
            duplicate_events=tuple(duplicates),
        )
