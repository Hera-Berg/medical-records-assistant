"""``GET /api/artifact/{hash}`` — the original bytes, and its sidecar facts.

The whole record rests on being able to follow a claim back to the page it was
read off. This is that route. A citation in the wiki points at a relative path
under ``raw/`` that still works with nothing installed; the same citation on
screen points here, and both resolve to the same file.

Two things it is careful about.

**It serves what the log describes, not what is in the folder.** An artefact is
in the record because an ``artifact.ingested`` event says so. A file sitting in
``raw/`` that no event describes is reported as unrecorded by ``health-agent
check`` and is not fetchable here — otherwise anything dropped into a synced
folder would be readable through the API.

**It assumes the bytes are hostile.** See :mod:`agent.server.serving`. The vault
syncs from third-party storage, so its contents are untrusted input regardless
of who owns the folder, and this route hands them to a browser on the same
origin as the SPA. Only an allowlist of media types renders inline; everything
else is served as a download, with sniffing off and a policy that executes
nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from ...ingest import sidecar as sidecar_mod
from .. import lookup, serialise, serving
from ..deps import get_index, get_state
from ..index import Index
from ..lookup import ArtifactAmbiguous, ArtifactNotFound
from ..state import RecordState

router = APIRouter()


def _resolve(state: RecordState, index: Index, token: str):
    snapshot = state.snapshot()
    try:
        short = lookup.resolve_short(snapshot, token, index)
    except ArtifactAmbiguous as exc:
        # 409, not 404 and not a guess. Showing the wrong photograph beside a
        # claim looks like an answer, which is worse than being asked for two
        # more characters.
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ArtifactNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return snapshot, short


@router.get("/api/artifact/{token}")
def artifact(
    token: str,
    request: Request,
    state: RecordState = Depends(get_state),
    index: Index = Depends(get_index),
):
    """The stored bytes, with the correct type and an inline disposition."""
    snapshot, short = _resolve(state, index, token)
    located = lookup.locate(state.vault, snapshot, short)

    if not located.exists:
        # Recorded but not on disk. Deliberately not a bare 404: the record says
        # this exists, so the answer is "the bytes are missing", which is a
        # problem to fix rather than a link that was wrong.
        raise HTTPException(
            status_code=410,
            detail=(
                f"artefact {short} is recorded in the event log but its bytes are "
                f"not in the vault. Restore it from a backup or the sync client's "
                f"trash, or re-ingest the same file. `health-agent check` lists "
                f"every artefact in this state."
            ),
        )

    artifact_record = located.artifact
    headers = serving.headers_for(
        artifact_record.mime, located.path.name, artifact_record.digest
    )
    if request.headers.get("if-none-match") == headers["etag"]:
        return JSONResponse(status_code=304, content=None, headers=headers)

    response = FileResponse(
        located.path,
        media_type=headers["content-type"],
        # Range support comes from FileResponse, which matters for seeking in a
        # long voice note and for a PDF viewer fetching one page at a time.
        stat_result=located.path.stat(),
    )
    # Set after construction: FileResponse derives an ETag from size and mtime,
    # and a resync or a restore-from-backup changes both while the content is
    # identical. The content hash is what actually identifies these bytes.
    for name, value in headers.items():
        response.headers[name] = value
    return response


@router.get("/api/artifact/{token}/meta")
def artifact_meta(
    token: str,
    state: RecordState = Depends(get_state),
    index: Index = Depends(get_index),
) -> dict[str, Any]:
    """What the record knows about one artefact, without fetching it.

    The four timestamps are reported as they stand, ``null`` included.
    ``captured_ts`` is null for anything that did not come from a live camera or
    microphone, and the interface says "added to the record" rather than "taken"
    when it is — the two are days apart whenever someone imports an old photo.
    """
    snapshot, short = _resolve(state, index, token)
    located = lookup.locate(state.vault, snapshot, short)

    extra: dict[str, Any] = {
        "present": located.exists,
        "bytes": located.path.stat().st_size if located.exists else None,
        "renders_inline": serving.may_render_inline(located.artifact.mime),
    }
    if located.exists:
        side = sidecar_mod.read(sidecar_mod.path_for(located.path))
        if side is not None:
            # The sidecar is what makes the folder self-describing with nothing
            # installed. Shown as it is, rather than merged into the fields
            # above, so a disagreement between it and the log stays visible.
            extra["sidecar"] = side
    extra["citation"] = serialise.citation(snapshot.citer, short)
    return serialise.artifact_summary(located.artifact, extra)


@router.get("/api/artifacts")
def artifacts(
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Every artefact in the record, newest first.

    Ordered by ``ingested_ts`` — the only one of the four timestamps that is
    always known. Ordering by capture time would silently reorder the list as
    EXIF reading fills that field in later.
    """
    snapshot = state.snapshot()
    rows = [
        serialise.artifact_summary(snapshot.artifacts[short])
        for short in sorted(
            snapshot.artifacts,
            key=lambda s: (snapshot.artifacts[s].ingested_ts or "", s),
            reverse=True,
        )
    ]
    return {"total": len(rows), "artifacts": rows, "as_of": snapshot.built_ts}
