"""What a reader said it could not read, as something waiting on a person.

The safety argument for a small local reader rests on this module. A reader that
abstains — "I cannot read the frequency of this medicine", "I cannot read this
page" — has only failed safely if a person finds out. So every latest reading of
an artefact that was unreadable, cut off, refused, or that carries abstentions
becomes a review item, high consequence, in the same queue as everything else.
It leaves the queue when the person says they have dealt with it
(``reading.acknowledged``), or when a newer reading of the artefact replaces the
one it was about.

Pure over the log, like the rest of the projection: the latest reading is chosen
by ``(ts, id)``, and an acknowledgement names the extraction event it answers,
so re-reading a document with a better reader raises a fresh item only if the
new reading also could not read something.

The summary names what could not be read — "Metformin: the frequency" — and never
a value. There is no value: an abstention is the absence of one.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..events.envelope import Event
from . import tiers

EXTRACTION_COMPLETED = "extraction.completed"
ACKNOWLEDGED = "reading.acknowledged"
COULD_NOT_READ = "could-not-read"

_FIELD_WORDS = {
    "name": "the name",
    "strength": "the strength",
    "frequency": "how often it is taken",
    "stopped": "whether it was stopped",
    "substance": "what the allergy is to",
    "reaction": "the reaction",
    "role": "the role",
    "whole entry": "an entry",
}

_FAMILY_WORDS = {
    "medications": "a medication",
    "allergies": "an allergy",
    "problems": "a condition or practitioner",
}


def _is_speech(event: Event) -> bool:
    reader = event.payload.get("reader")
    if isinstance(reader, str):
        return reader == "speech"
    return "transcript" in event.payload and "turns" not in event.payload


def latest_readings(events: Iterable[Event]) -> dict[str, Event]:
    """The newest reading of each artefact by the vision-language reader."""
    latest: dict[str, Event] = {}
    for event in events:
        if event.type != EXTRACTION_COMPLETED or _is_speech(event):
            continue
        short = event.payload.get("artifact")
        if not isinstance(short, str) or not short:
            continue
        current = latest.get(short)
        if current is None or event.sort_key > current.sort_key:
            latest[short] = event
    return latest


def acknowledged(events: Iterable[Event]) -> set[str]:
    """Extraction event ids a person has said they dealt with."""
    return {
        target
        for event in events
        if event.type == ACKNOWLEDGED
        and isinstance(target := event.payload.get("target"), str)
    }


def open_abstentions(event: Event) -> list[dict[str, Any]]:
    items = event.payload.get("abstentions")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def needs_a_person(event: Event) -> bool:
    """Whether this reading left something a person has to look at."""
    return event.payload.get("readable") is False or bool(open_abstentions(event))


def describe(item: dict[str, Any]) -> str:
    field = _FIELD_WORDS.get(str(item.get("field")), str(item.get("field") or "part of it"))
    name = item.get("subject_name")
    if isinstance(name, str) and name.strip():
        return f"{name.strip()}: {field}"
    family = _FAMILY_WORDS.get(str(item.get("family")), "something")
    return f"{family} — {field}" if field != "an entry" else f"{family} it could not read at all"


def summary(event: Event) -> str:
    if event.payload.get("readable") is False:
        if event.payload.get("truncated"):
            why = "the reader ran out of room before it finished"
        else:
            reason = event.payload.get("unreadable_reason")
            why = " ".join(str(reason).split()) if reason else "the reader could not read it"
        return (
            f"This document could not be read ({why}). Nothing was taken from it. "
            f"Check it yourself, photograph it again, or type in what it says."
        )
    parts = [describe(item) for item in open_abstentions(event)]
    return (
        "Parts of this document could not be read with certainty, so nothing was "
        "guessed for them: " + "; ".join(parts) + ". Check these against the "
        "document, and photograph it again or type in what matters."
    )


def review_items(events: Iterable[Event]):
    """A ``could-not-read`` review item for every reading still waiting on a person."""
    from .reconcile import ReviewItem  # noqa: PLC0415 - import cycle

    events = list(events)
    done = acknowledged(events)
    items = []
    for short, event in sorted(latest_readings(events).items()):
        if event.id in done or not needs_a_person(event):
            continue
        items.append(
            ReviewItem(
                kind=COULD_NOT_READ,
                # High, whatever was unread: an unread page or an unread dose can
                # hold a medication, and "nothing is added without a tap" is the
                # promise a missed medication would otherwise break silently.
                consequence=tiers.HIGH,
                subject_id=f"artifact:{short}",
                predicate="",
                summary=summary(event),
                cite=short,
                targets=(event.id,),
            )
        )
    return tuple(items)
