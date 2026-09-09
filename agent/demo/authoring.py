"""Authoring event objects by hand, without a model.

Phase 3 has no extractor, so every claim in the test suite is written by hand,
and the demo vault is seeded the same way. These are the constructors for that:
they build well-formed events of each type with the fields the projection reads,
and nothing else.

They live in the package rather than in ``tests/`` because the demo command needs
exactly the same constructors, and two copies of "what a valid claim payload looks
like" would drift apart in the direction that matters least until it mattered a
lot. ``tests/conftest.py`` re-exports these names.

Nothing here writes to a vault or reads the clock. A caller supplies every
timestamp, which is what lets both the suite and the demo build a stream whose
ordering is the thing under test.
"""

from __future__ import annotations

from ..events import envelope

DEFAULT_ARTIFACT = "a3f91c"


def note(device: str, ts: str | None = None, **payload) -> envelope.Event:
    """A user-authored note event, the simplest valid event."""
    return envelope.new("note.recorded", device, payload=payload or {"text": "x"}, ts=ts)


def claim(
    device: str,
    subject: str,
    predicate: str,
    value,
    tier: str = "prescriber-issued",
    ts: str | None = None,
    artifact: str = DEFAULT_ARTIFACT,
    occurred=None,
    dispense=None,
    consequence: str | None = None,
    confidence: float | None = None,
    **extra,
) -> envelope.Event:
    """A ``claim.proposed`` event, as the extraction phase would emit one."""
    payload = {
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "evidence_tier": tier,
        "occurred_at": occurred,
        "artifact_ts": None,
        "captured_ts": None,
        "ingested_ts": ts,
    }
    if dispense is not None:
        payload["dispense"] = dispense
    if consequence is not None:
        payload["consequence"] = consequence
    if confidence is not None:
        payload["confidence"] = confidence
    payload.update(extra)
    return envelope.new(
        "claim.proposed",
        device,
        payload=payload,
        provenance={
            "model": "qwen3.5:9b",
            "model_rev": "sha256:1111",
            "prompt_hash": "sha256:2222",
            "artifact": artifact,
        },
        ts=ts,
    )


def confirm(device: str, target: str, ts: str | None = None) -> envelope.Event:
    return envelope.new("claim.confirmed", device, payload={"target": target}, ts=ts)


def reject(device: str, target: str, ts: str | None = None) -> envelope.Event:
    return envelope.new("claim.rejected", device, payload={"target": target}, ts=ts)


def correct(
    device: str,
    value=None,
    target: str | None = None,
    subject: str | None = None,
    predicate: str | None = None,
    ts: str | None = None,
    **extra,
) -> envelope.Event:
    """A ``claim.corrected`` event — the highest authority in the record."""
    payload: dict = {}
    if target is not None:
        payload["target"] = target
    if subject is not None:
        payload["subject"] = subject
    if predicate is not None:
        payload["predicate"] = predicate
    if value is not None:
        payload["value"] = value
    payload.update(extra)
    return envelope.new("claim.corrected", device, payload=payload, ts=ts)


def ingested(
    device: str,
    short: str = DEFAULT_ARTIFACT,
    ts: str = "2026-09-02T09:14:03Z",
    mime: str = "image/jpeg",
    path: str | None = None,
    artifact_ts: str | None = None,
    captured_ts: str | None = None,
    source: str = "camera",
) -> envelope.Event:
    """An ``artifact.ingested`` event, so citations have something to resolve to."""
    ext = {"image/jpeg": "jpg", "application/pdf": "pdf", "audio/webm": "webm"}.get(mime, "bin")
    return envelope.new(
        "artifact.ingested",
        device,
        ts=ts,
        payload={
            "hash": (short * 11)[:64],
            "short": short,
            "path": path or f"raw/2026/09/2026-09-02T0914Z_{short}.{ext}",
            "sidecar": f"raw/2026/09/2026-09-02T0914Z_{short}.{ext}.json",
            "bytes": 2048,
            "mime": mime,
            "mime_source": "sniff",
            "declared_mime": None,
            "ingested_ts": ts,
            "captured_ts": captured_ts,
            "artifact_ts": artifact_ts,
            "capture": {
                "source": source,
                "original_filename": None,
                "source_mtime_hint": None,
                "note": None,
            },
        },
    )


def merge(
    device: str, source: str, into: str, ts: str | None = None, reverted: bool = False
) -> envelope.Event:
    return envelope.new(
        "entity.merge.reverted" if reverted else "entity.merge.confirmed",
        device,
        payload={"from": source, "into": into},
        ts=ts,
    )


def on_day(day: int, hour: int = 9, month: int = 9, year: int = 2026) -> str:
    """A canonical timestamp, for streams whose ordering is the thing under test."""
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00:00Z"
