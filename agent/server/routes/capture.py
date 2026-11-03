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

**``captured_ts`` is null on every path this route serves but one.** A browser
gives ``File.lastModified`` for a picked or dropped file, and that is a
filesystem mtime: rewritten by downloads, copies and sync clients. It is
recorded as ``source_mtime_hint``, which is what it is. Drag in a photo taken
three days ago and a substituted capture time is wrong by three days with
nothing about the record looking wrong.

The exception is ``source=recorder``. A recording made by the microphone in
this tab genuinely happened at a moment this page watched happen, so that
moment is sent and stored — it is the first path in the application allowed to
set ``captured_ts``, and the reason the field exists. It is still checked
rather than trusted: canonical UTC, and not in the future by more than a
minute's clock skew. A browser clock can be wrong, and a capture time in 2031
would sort a voice note to the end of the timeline for ever.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse

from ... import ingest as ingest_mod
from ...errors import DeviceIdentityError, HealthAgentError, IngestError
from ...events import envelope
from ..deps import get_state
from ..state import RecordState

router = APIRouter()

#: Capture sources a browser may claim. ``cli`` and ``import`` are not here:
#: they are the terminal's. ``camera`` is not here either — the mobile camera
#: input arrives as an ordinary file upload and a browser does not tell us when
#: the shutter fired, so claiming it did would be the substitution this whole
#: route is careful about. ``chat`` is a file dropped into Ask, or a message the
#: person chose to add to their record from it.
WEB_SOURCES = frozenset({"upload", "paste", "drop", "recorder", "chat"})

#: Sources that genuinely know when the bytes were made, because the page
#: watched them being made.
LIVE_SOURCES = frozenset({"recorder"})

#: How far ahead of this machine a browser's clock may be before its capture
#: time is refused. Small: this is skew, not a timezone, and the two clocks are
#: usually the same clock.
MAX_CLOCK_SKEW_S = 60


@router.post("/api/capture", status_code=202)
def capture(
    request: Request,
    files: list[UploadFile],
    source: str = Form("upload"),
    note: str | None = Form(None),
    captured_ts: str | None = Form(None),
    author: str = Form(""),
    role: str = Form(""),
    observation_date: str = Form(""),
    state: RecordState = Depends(get_state),
) -> JSONResponse:
    """Take one or more files into the record and queue them to be read.

    ``202``, not ``200``: the bytes are stored and the record is updated, and the
    reading of them has been accepted rather than done. Per-file results are
    reported individually — one unreadable file out of five must not lose the
    other four, and a partial success the caller cannot see is worse than a
    failure it can.
    """
    metadata = _submission_metadata(state, author, role, observation_date)
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

    captured = _captured_ts(state, source, captured_ts)

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
                result = _store_one(state, upload, source, note, captured)
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
            if request.url.path.startswith('/api/clinician/') and result.status == ingest_mod.STATUS_STORED:
                from ..review_workflow import enabled
                if enabled(state.snapshot().events):state.append(envelope.new('note.recorded',state.vault.identity.id,payload={'clinical_upload':result.short,'author':request.state.clinician['name']}))
            if result.status == ingest_mod.STATUS_STORED and metadata:
                state.append(envelope.new("note.recorded",state.vault.identity.id,payload={"submission_artifact":result.short,**metadata}))
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
                    "url": f"{'/api/clinician' if request.url.path.startswith('/api/clinician/') else '/api'}/artifact/{result.short}",
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
            # The promise this route makes, said plainly — but only when it
            # was kept. "Stored and queued" printed above `accepted: 0` is a
            # reassuring sentence next to a failure, and a person skimming
            # reads the sentence.
            "note": _note(len(results) - failures, failures),
        },
    )


def _note(accepted: int, failed: int) -> str:
    """What actually happened, in one sentence, matching the counts beside it."""
    if accepted == 0:
        return (
            "Nothing was stored. The files you sent are still where they were — "
            "nothing has been taken into the record."
        )
    stored = f"{accepted} {'file' if accepted == 1 else 'files'} stored and queued"
    kept = "Nothing has been read yet, and nothing reaches the record until it has been."
    if failed:
        return (
            f"{stored}; {failed} could not be stored and {'is' if failed == 1 else 'are'} "
            f"listed above. {kept}"
        )
    return f"{stored}. {kept}"


def _captured_ts(state: RecordState, source: str, value: str | None) -> str | None:
    """The moment the bytes were made, where that is genuinely known.

    Accepted only from a live source, and refused rather than adjusted when it
    does not make sense. Silently clamping a bad clock would produce a plausible
    wrong date, which is worse here than an error: the whole point of keeping
    four timestamps apart is that a wrong one is invisible once written.
    """
    if value is None or not value.strip():
        return None
    if source not in LIVE_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"a capture time was sent with source {source!r}, which cannot know "
                f"one. Only a recording made in this page knows when it was made; "
                f"for anything else the file's modification time is not when the "
                f"photograph was taken, and the record stores an explicit null"
            ),
        )
    if not envelope.is_canonical_ts(value):
        raise HTTPException(
            status_code=400,
            detail=(
                f"captured_ts {value!r} must be canonical UTC of the form "
                f"YYYY-MM-DDTHH:MM:SSZ"
            ),
        )
    moment = envelope.parse_ts_or_none(value)
    if moment is not None and (moment - state.now()).total_seconds() > MAX_CLOCK_SKEW_S:
        raise HTTPException(
            status_code=400,
            detail=(
                f"captured_ts {value} is in the future. This machine's clock or the "
                f"browser's is wrong, and a recording dated ahead of today would sit "
                f"at the end of your timeline until that date passes"
            ),
        )
    return value


def _store_one(
    state: RecordState,
    upload: UploadFile,
    source: str,
    note: str | None,
    captured_ts: str | None = None,
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
        # Null on every path but the recorder. See the module docstring — a
        # browser cannot tell us when a photograph was taken, only when its file
        # was last written, but it can tell us when its own microphone ran.
        captured_ts=captured_ts,
        source_mtime_hint=None,
        note=note,
    )
    return ingest_mod.ingest_fileobj(state.vault, upload.file, context)


@router.post("/api/capture/text", status_code=202)
def capture_text(
    request: Request,
    text: str = Form(...),
    author: str = Form(""),
    role: str = Form(""),
    observation_date: str = Form(""),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Record a typed note.

    A note is not an artefact: there are no bytes to keep beside it and nothing
    to read with a model, so it appends ``note.recorded`` and queues nothing.
    It is ``patient-reported`` by construction — the user typed it — and it
    lands on the timeline at the moment it was recorded, which is the only date
    anything knows about it.
    """
    metadata = _submission_metadata(state, author, role, observation_date)
    body = text.strip()
    if not body:
        raise HTTPException(status_code=400, detail="an empty note records nothing")

    identity = state.vault.identity
    event = envelope.new(
        "note.recorded",
        identity.id,
        actor="user",
        payload={**metadata, "text": body, "source": "typed", "submission_workspace":"clinician" if request.url.path.startswith("/api/clinician/") else "patient-carer"},
    )
    state.append(event)
    return {"event": event.id, "ts": event.ts, "text": body}


def _submission_metadata(state, author, role, observation_date):
    from datetime import date
    from zoneinfo import ZoneInfo
    result = {}
    for key,value in (("author",author),("role",role)):
        if len(value)>120:raise HTTPException(400,"Name and role must be 120 characters or fewer.")
        if value.strip():result[key]=value.strip()
    if observation_date:
        try:
            parsed=date.fromisoformat(observation_date)
            if parsed.isoformat()!=observation_date or parsed>state.now().astimezone(ZoneInfo('Australia/Sydney')).date():raise ValueError()
        except ValueError:raise HTTPException(400,"Choose a valid observation date that is not in the future.")
        result['observation_date']=observation_date
    return result
