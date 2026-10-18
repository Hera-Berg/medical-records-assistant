"""Original document metadata, without importing illustrative example records."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from ...events.envelope import parse_ts_or_none
from ..deps import get_state
from ..state import RecordState

router = APIRouter()


def _date(stamp):
    moment = parse_ts_or_none(stamp)
    return moment.date().isoformat() if moment else None


def _kind(mime):
    if mime == "application/pdf":
        return "PDF document"
    for prefix, label in (("image/", "Image"), ("audio/", "Audio recording"), ("video/", "Video recording"), ("text/", "Text document")):
        if mime.startswith(prefix):
            return label
    return "Document"


@router.get("/api/documents")
def documents(state: RecordState = Depends(get_state)):
    snapshot = state.snapshot()
    originals = {}
    for event in snapshot.events:
        if event.type != "artifact.ingested":
            continue
        capture = event.payload.get("capture")
        name = capture.get("original_filename") if isinstance(capture, dict) else None
        short = event.payload.get("short")
        if isinstance(short, str) and isinstance(name, str) and name.strip():
            originals[short] = name.replace("\\", "/").rsplit("/", 1)[-1]
    items = []
    for short, artifact in snapshot.artifacts.items():
        items.append({
            "id": short,
            "title": originals.get(short) or artifact.rel.replace("\\", "/").rsplit("/", 1)[-1],
            "category": _kind(artifact.mime),
            "document_date": _date(artifact.artifact_ts),
            "added_date": _date(artifact.ingested_ts),
            "mime": artifact.mime,
            "url": f"/artifact/{short}",
            "example": False,
        })
    items.sort(key=lambda item: (item["document_date"] or item["added_date"] or "", item["id"]), reverse=True)
    return {"documents": items, "total": len(items)}
