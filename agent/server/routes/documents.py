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
    from ...demo_metadata import entries, source_ids, SCENARIO
    demo = entries(snapshot.events, snapshot.artifacts)
    by_source = {e.payload['artifact']: e for e in demo.values()}
    hidden_originals = source_ids(snapshot.events, snapshot.artifacts) - by_source.keys()
    items = []
    for short, artifact in snapshot.artifacts.items():
        if short in hidden_originals:
            continue  # UTF-8 wording remains available by its original-file link.
        item = {
            "id": short,
            "title": originals.get(short) or artifact.rel.replace("\\", "/").rsplit("/", 1)[-1],
            "category": _kind(artifact.mime),
            "document_date": _date(artifact.artifact_ts),
            "added_date": _date(artifact.ingested_ts),
            "mime": artifact.mime,
            "url": f"/artifact/{short}",
            "example": False,
        }
        if short in by_source:
            p = by_source[short].payload
            item.update(title=p['title'], category=p['source_type'], document_date=p['event_date'],
                        professional=p['author'], demo=True, event_type=p['event_type'],
                        release_note='Fictional demo - patient-visible authored source; not clinician verification')
        items.append(item)
    items.sort(key=lambda item: (item["document_date"] or item["added_date"] or "", item["id"]), reverse=True)
    return {"documents": items, "total": len(items), **({"demo_scenario": SCENARIO} if demo else {})}
