"""Ingest: taking a file into the vault.

One action, classified later. Something arrives — a photographed script, a
pathology PDF, a voice recording — and this module puts the original bytes in
``raw/`` untouched, writes a sidecar describing them, and appends one event.
Nothing here reads the document. No model runs, no OCR runs, and no claim is
made about what the file says.

**Order matters, and it is bytes, then sidecar, then event.** A crash between
any two steps leaves a file with no event, which ``health-agent check`` reports
as unrecorded and which the next ingest of the same content adopts. The reverse
order would leave an event with no file, which is a citation in the wiki
pointing at nothing — the failure this record cannot tolerate. For the same
reason, nothing in this module deletes bytes from ``raw/`` to tidy up after a
failure.

Deduplication is by content hash, because people photograph the same script
twice and download the same pathology PDF three times. The second arrival is not
an error and is not discarded: it becomes an ``artifact.reseen`` event carrying
its own capture context, so the record knows the script was still in the
patient's hand in September.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from ..errors import IngestError
from ..events import envelope
from ..events.envelope import Event
from . import hashing, mime, naming, sidecar
from .store import IngestedRecord, RawStore, ingested_records

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from ..vault import Vault

#: Where bytes came from. Recorded, never inferred, and deliberately not a
#: document category: what kind of document this is, is the model's job later.
CAPTURE_SOURCES = frozenset(
    {"cli", "import", "upload", "paste", "drop", "camera", "recorder"}
)

STATUS_STORED = "stored"
STATUS_RESEEN = "reseen"
STATUS_RESTORED = "restored"


@dataclass(frozen=True)
class CaptureContext:
    """What the caller knows about where these bytes came from.

    ``captured_ts`` is the moment the photo was taken or the audio recorded. It
    is set only where that is genuinely known — the live camera and microphone
    paths, and EXIF once that is read — and is otherwise ``None``, which is
    written to the record as an explicit ``null``. Ingest time is never used to
    fill it: a photo dragged in three days after it was taken would acquire a
    capture date three days late, and nothing about the record would look wrong.

    ``source_mtime_hint`` is the modification time of the file we copied from.
    It is a hint and is named as one. Sync clients, downloads and copies all
    rewrite it, so it is recorded for a human to weigh and is never promoted to
    ``captured_ts`` or ``artifact_ts``.
    """

    source: str = "import"
    original_filename: str | None = None
    declared_mime: str | None = None
    captured_ts: str | None = None
    source_mtime_hint: str | None = None
    note: str | None = None

    def validate(self) -> None:
        if self.source not in CAPTURE_SOURCES:
            raise IngestError(
                f"unknown capture source {self.source!r}; expected one of "
                f"{', '.join(sorted(CAPTURE_SOURCES))}"
            )
        if self.captured_ts is not None and not envelope.is_canonical_ts(self.captured_ts):
            raise IngestError(
                f"captured_ts {self.captured_ts!r} must be canonical UTC of the form "
                f"YYYY-MM-DDTHH:MM:SSZ, or absent where it is not known"
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "original_filename": self.original_filename,
            "source_mtime_hint": self.source_mtime_hint,
            "note": self.note,
        }


@dataclass(frozen=True)
class IngestResult:
    """What happened to one file."""

    status: str
    digest: str
    short: str
    path: Path
    rel: str
    size: int
    mime: str
    event: Event

    @property
    def is_new_content(self) -> bool:
        return self.status == STATUS_STORED

    def describe(self) -> str:
        if self.status == STATUS_STORED:
            return f"stored   {self.rel}  {self.mime}  {self.size} bytes"
        if self.status == STATUS_RESTORED:
            return f"restored {self.rel}  (bytes were missing; already in the record)"
        return f"reseen   {self.rel}  (identical content already ingested)"


def _mtime_hint(path: Path) -> str | None:
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    return envelope.format_ts(datetime.fromtimestamp(stamp, tz=timezone.utc))


def ingest_path(
    vault: Vault,
    source_path: str | os.PathLike[str],
    context: CaptureContext | None = None,
) -> IngestResult:
    """Take the file at *source_path* into the vault. The original is copied, not moved."""
    path = Path(source_path).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise IngestError(f"cannot ingest {path}: {exc}") from exc
    if not resolved.is_file():
        raise IngestError(f"cannot ingest {resolved}: not a regular file")

    raw_dir = RawStore(vault.root, vault.profile).raw_dir
    if resolved == raw_dir or raw_dir in resolved.parents:
        raise IngestError(
            f"{resolved} is already inside the vault's raw store; ingesting it again "
            f"would record the same bytes twice under a second name"
        )

    context = context or CaptureContext()
    context = CaptureContext(
        source=context.source,
        original_filename=context.original_filename or resolved.name,
        declared_mime=context.declared_mime,
        captured_ts=context.captured_ts,
        source_mtime_hint=context.source_mtime_hint or _mtime_hint(resolved),
        note=context.note,
    )
    return _ingest(vault, lambda: open(resolved, "rb"), context)


def ingest_bytes(
    vault: Vault,
    data: bytes,
    context: CaptureContext | None = None,
) -> IngestResult:
    """Take *data* into the vault. The path for uploads, pastes and recordings."""
    return _ingest(vault, lambda: io.BytesIO(data), context or CaptureContext(source="upload"))


def _ingest(vault: Vault, opener, context: CaptureContext) -> IngestResult:
    context.validate()
    # Refuse before writing anything if this machine must not append: bytes in
    # raw/ with no event are recoverable, but there is no reason to create the
    # situation deliberately.
    identity = vault.identity
    identity.require_appendable()

    ingested_ts = envelope.now_ts()
    store = RawStore(vault.root, vault.profile)

    with opener() as handle:
        staged = store.stage(handle, ingested_ts)

    if staged.size == 0:
        store.discard(staged)
        raise IngestError(
            "refusing to ingest an empty file: zero bytes is a failed copy or a "
            "failed upload, not an artefact"
        )

    records, _ = ingested_records(vault.read().events)
    existing = records.get(staged.digest)
    detected = mime.detect(staged.head, context.original_filename, context.declared_mime)
    extension = detected.extension(context.original_filename)

    if existing is not None:
        return _record_reseen(
            vault, store, staged, existing, context, ingested_ts, detected, extension
        )

    path = store.commit(staged, ingested_ts, extension)
    store.verify_stored(path, staged.digest)
    rel = store.relative(path)
    short = _short_of(path, staged.digest)

    event = envelope.new(
        "artifact.ingested",
        identity.id,
        ts=ingested_ts,
        payload={
            "hash": staged.digest,
            "short": short,
            "path": rel,
            "sidecar": store.relative(sidecar.path_for(path)),
            "bytes": staged.size,
            "mime": detected.mime,
            "mime_source": detected.source,
            "declared_mime": context.declared_mime,
            "ingested_ts": ingested_ts,
            "captured_ts": context.captured_ts,
            "artifact_ts": None,
            "capture": context.to_payload(),
        },
    )

    sidecar.write(
        sidecar.path_for(path),
        sidecar.build(
            artifact=path.name,
            digest=staged.digest,
            short=short,
            size=staged.size,
            mime=detected.mime,
            mime_source=detected.source,
            declared_mime=context.declared_mime,
            ingested_ts=ingested_ts,
            captured_ts=context.captured_ts,
            artifact_ts=None,
            capture=context.to_payload(),
            event_id=event.id,
        ),
    )

    vault.append(event)
    return IngestResult(
        status=STATUS_STORED,
        digest=staged.digest,
        short=short,
        path=path,
        rel=rel,
        size=staged.size,
        mime=detected.mime,
        event=event,
    )


def _short_of(path: Path, digest: str) -> str:
    """The hash prefix as it appears in the stored filename.

    Read off the name rather than recomputed, because a prefix collision makes
    the stored one longer than the default six characters.
    """
    parsed = naming.parse(path.name)
    return parsed.short if parsed else digest[: naming.SHORT_HASH_CHARS]


def _record_reseen(
    vault: Vault,
    store: RawStore,
    staged,
    existing: IngestedRecord,
    context: CaptureContext,
    ingested_ts: str,
    detected: mime.Detected,
    extension: str,
) -> IngestResult:
    """Handle content the log has already seen.

    Two cases share one event type. Usually the bytes are already on disk and
    the staged copy is dropped. Occasionally the log records an artefact whose
    file has gone — deleted by hand, lost in a restore, not yet synced back — and
    the same bytes have turned up again. Those are written back to the path the
    log already cites, so existing citations keep resolving, and the event is
    still ``artifact.reseen``: this content entered the record once, and saying
    otherwise would double-count the evidence.
    """
    target = store.resolve_recorded(existing.rel)
    if target is None:
        target = store.find(staged.digest)

    if target is not None and target.exists():
        store.discard(staged)
        path = target
        status = STATUS_RESEEN
    else:
        if target is not None:
            path = store.commit_to(staged, target)
        else:
            path = store.commit(staged, existing.ingested_ts or ingested_ts, extension)
        store.verify_stored(path, staged.digest)
        status = STATUS_RESTORED

    rel = store.relative(path)
    payload: dict[str, Any] = {
        "hash": staged.digest,
        "short": _short_of(path, staged.digest),
        "path": rel,
        "bytes": staged.size,
        "mime": existing.mime or detected.mime,
        "ingested_ts": ingested_ts,
        "captured_ts": context.captured_ts,
        "artifact_ts": None,
        "capture": context.to_payload(),
        "first_ingested": {
            "event": existing.event_id,
            "ingested_ts": existing.ingested_ts,
        },
    }
    if status == STATUS_RESTORED:
        payload["restored"] = True

    event = envelope.new(
        "artifact.reseen", vault.identity.id, ts=ingested_ts, payload=payload
    )

    # A restored artefact may have lost its sidecar with its bytes. Rebuild it
    # from what the log knows, crediting the original ingest rather than this one.
    side = sidecar.path_for(path)
    if status == STATUS_RESTORED and not side.exists():
        sidecar.write(
            side,
            sidecar.build(
                artifact=path.name,
                digest=staged.digest,
                short=_short_of(path, staged.digest),
                size=staged.size,
                mime=existing.mime or detected.mime,
                mime_source=detected.source,
                declared_mime=context.declared_mime,
                ingested_ts=existing.ingested_ts,
                captured_ts=None,
                artifact_ts=None,
                capture={**context.to_payload(), "restored_ts": ingested_ts},
                event_id=existing.event_id,
            ),
        )

    vault.append(event)
    return IngestResult(
        status=status,
        digest=staged.digest,
        short=payload["short"],
        path=path,
        rel=rel,
        size=staged.size,
        mime=str(payload["mime"]),
        event=event,
    )


__all__ = [
    "CAPTURE_SOURCES",
    "CaptureContext",
    "IngestResult",
    "RawStore",
    "STATUS_RESEEN",
    "STATUS_RESTORED",
    "STATUS_STORED",
    "hashing",
    "ingest_bytes",
    "ingest_path",
    "ingested_records",
    "mime",
    "naming",
    "sidecar",
]
