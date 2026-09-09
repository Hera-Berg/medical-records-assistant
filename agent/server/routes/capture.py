"""``POST /api/capture`` — bytes in, and an answer before anything reads them.

``CLAUDE.md``: "``/api/capture`` must return before any inference runs. The user
is in a waiting room; never block the UI on a 9B model." That sentence is the
whole design of this route. What happens here is: stream the bytes into
``raw/``, write the sidecar, append one event, queue a job, answer. The
inference box is not contacted, is not checked for reachability, and does not
need to exist. ``MODELS.md`` puts the same requirement from the other side —
"Capture never depends on the endpoint … A capture that fails because a Mac was
asleep is unacceptable" — and there is a test that refuses the endpoint at the
socket level and asserts a capture still succeeds.

**One action, classified later.** No category, no title, no tags. What kind of
document this is, is the model's job, and asking the user at capture time is
asking them to do it in a corridor.

**``captured_ts`` is null on every path this route serves.** A browser gives
``File.lastModified`` for a picked or dropped file, and that is a filesystem
mtime: rewritten by downloads, copies and sync clients. It is recorded as
``source_mtime_hint``, which is what it is. Drag in a photo taken three days ago
and a substituted capture time is wrong by three days with nothing about the
record looking wrong. The live camera and microphone paths genuinely know the
moment and set it themselves; that is phase 6.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ... import ingest as ingest_mod
from ...errors import DeviceIdentityError, HealthAgentError, IngestError
from ...events import envelope
from ..deps import get_state
from ..state import RecordState

router = APIRouter()

#: Capture sources a browser may claim. The rest of
#: :data:`agent.ingest.CAPTURE_SOURCES` belongs to paths that are not this one:
#: ``cli`` and ``import`` are the terminal's, and ``camera`` and ``recorder``
#: are phase 6's, where ``captured_ts`` is genuinely known.
WEB_SOURCES = frozenset({"upload", "paste", "drop"})


@router.post("/api/capture", status_code=202)
def capture(
    files: list[UploadFile],
    source: str = Form("upload"),
    note: str | None = Form(None),
    state: RecordState = Depends(get_state),
) -> JSONResponse:
    """Take one or more files into the record and queue them to be read.

    ``202``, not ``200``: the bytes are stored and the record is updated, and the
    reading of them has been accepted rather than done. Per-file results are
    reported individually — one unreadable file out of five must not lose the
    other four, and a partial success the caller cannot see is worse than a
    failure it can.
    """
    if source not in WEB_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown capture source {source!r}; this route accepts "
                f"{', '.join(sorted(WEB_SOURCES))}"
            ),
        )
    if not files:
        raise HTTPException(status_code=400, detail="no files were sent")

    results: list[dict[str, Any]] = []
    queued: list[str] = []
    failures = 0

    # One lock for the whole batch. Every file appends to the same shard, and
    # `log.append` inspects the last byte before writing — two uploads landing
    # together would interleave that check with each other's write.
    with state.lock:
        queue = state.queue()
        for upload in files:
            try:
                result = _store_one(state, upload, source, note)
            except (IngestError, DeviceIdentityError) as exc:
                failures += 1
                results.append(
                    {
                        "filename": upload.filename,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue
            except HealthAgentError as exc:  # noqa: PERF203 - per-file reporting
                failures += 1
                results.append(
                    {"filename": upload.filename, "status": "failed", "error": str(exc)}
                )
                continue

            # Queued for every artefact, including one already in the record: a
            # duplicate is not re-ingested, but `Queue.add` is idempotent on the
            # artefact and re-queuing one whose read never completed is how a
            # job lost to a crash gets picked up again.
            job = queue.add(result.short)
            queued.append(result.short)
            results.append(
                {
                    "filename": upload.filename,
                    "status": result.status,
                    "hash": result.digest,
                    "short": result.short,
                    "path": result.rel,
                    "bytes": result.size,
                    "mime": result.mime,
                    "event": result.event.id,
                    "job": job.id,
                    "url": f"/api/artifact/{result.short}",
                }
            )
        state.invalidate()
        depth = queue.depth()

    # The worker is nudged after the lock is released, and it is only a nudge:
    # if it is not running, or the box is asleep, the jobs are on disk and the
    # next drain finds them. Nothing about this response depends on it.
    worker = getattr(state, "worker", None)
    if worker is not None:
        worker.nudge()

    return JSONResponse(
        status_code=207 if failures and results else 202,
        content={
            "accepted": len(results) - failures,
            "failed": failures,
            "queued": queued,
            "queue_depth": depth,
            "results": results,
            # Said plainly, every time, because it is the promise this route
            # makes: the bytes are safe and nothing has read them yet.
            "note": (
                "Stored and queued. Nothing has been read yet, and nothing reaches "
                "the record until it has been."
            ),
        },
    )


def _store_one(
    state: RecordState, upload: UploadFile, source: str, note: str | None
) -> ingest_mod.IngestResult:
    """Stream one upload into ``raw/``.

    The declared content type is passed as *declared* and nothing more: mime
    detection sniffs the bytes, and a browser's guess from a file extension is
    the weakest evidence available about what a file actually is.
    """
    context = ingest_mod.CaptureContext(
        source=source,
        original_filename=upload.filename,
        declared_mime=upload.content_type,
        # Explicitly null. See the module docstring — a browser cannot tell us
        # when a photograph was taken, only when its file was last written.
        captured_ts=None,
        source_mtime_hint=None,
        note=note,
    )
    return ingest_mod.ingest_fileobj(state.vault, upload.file, context)


@router.post("/api/capture/text", status_code=202)
def capture_text(
    text: str = Form(...),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Record a typed note.

    A note is not an artefact: there are no bytes to keep beside it and nothing
    to read with a model, so it appends ``note.recorded`` and queues nothing.
    It is ``patient-reported`` by construction — the user typed it — and it
    lands on the timeline at the moment it was recorded, which is the only date
    anything knows about it.
    """
    body = text.strip()
    if not body:
        raise HTTPException(status_code=400, detail="an empty note records nothing")

    identity = state.vault.identity
    event = envelope.new(
        "note.recorded",
        identity.id,
        actor="user",
        payload={"text": body, "source": "typed"},
    )
    state.append(event)
    return {"event": event.id, "ts": event.ts, "text": body}
