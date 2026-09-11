"""The event envelope: the shape of every line in the log.

Phase 1 validates the envelope and nothing inside ``payload``. Claim structure,
evidence tiers and consequence tiers belong to the projection and extraction
phases; the log's job here is to guarantee that every line is well formed,
attributable to a device and an actor, and orderable.

Validation is strict on **append** and permissive on **read**. A line already on
disk is history: an unknown event type or an unexpected extra key is carried
through and reported, never dropped. Only what this build writes is held to the
current registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..device import is_valid_device_id
from ..errors import EventValidationError
from . import ulid

ACTOR_USER = "user"
ACTOR_AGENT = "agent"
ACTORS = (ACTOR_USER, ACTOR_AGENT)

#: Event type -> the only actor permitted to author it (CLAUDE.md, "Event types").
EVENT_TYPES: dict[str, str] = {
    "artifact.ingested": ACTOR_USER,
    "artifact.reseen": ACTOR_USER,
    "extraction.completed": ACTOR_AGENT,
    "claim.proposed": ACTOR_AGENT,
    "claim.confirmed": ACTOR_USER,
    "claim.rejected": ACTOR_USER,
    "claim.corrected": ACTOR_USER,
    "entity.merge.proposed": ACTOR_AGENT,
    "entity.merge.confirmed": ACTOR_USER,
    "entity.merge.reverted": ACTOR_USER,
    "note.recorded": ACTOR_USER,
    # A consultation summary was prepared and written to `exports/`. A user act:
    # the patient typed the question and chose the moment. The payload records
    # what it was built from — the question, the reference point, `as_of`, and
    # the claims it cited — never the rendered sheet, which is re-derived from
    # the log truncated at this event. See `agent.summary.store`.
    "summary.generated": ACTOR_USER,
    # The model registry. A remote model has no content hash to pin, so its
    # identity is the string the server reported, recorded the first time it
    # is seen. See MODELS.md, "Model identity".
    "model.identity.observed": ACTOR_AGENT,
}

ENVELOPE_KEYS = ("id", "type", "ts", "device", "actor", "provenance", "payload")

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def format_ts(moment: datetime) -> str:
    """Render *moment* in the log's canonical timestamp form."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(TS_FORMAT)


def now_ts() -> str:
    return format_ts(datetime.now(timezone.utc))


def is_canonical_ts(value: object) -> bool:
    return isinstance(value, str) and bool(_TS_RE.match(value)) and parse_ts_or_none(value) is not None


def parse_ts_or_none(value: object) -> datetime | None:
    """Parse a timestamp to aware UTC, tolerantly. ``None`` if unparseable.

    Reads accept fractional seconds and non-UTC offsets even though writes never
    produce them, because ordering must still be correct for a hand-edited or
    externally-generated line. Ordering compares parsed instants, never strings:
    ``"...11.5Z"`` sorts before ``"...11Z"`` as text, which would be wrong.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Event:
    """One line of the event log."""

    id: str
    type: str
    ts: str
    device: str
    actor: str
    provenance: dict[str, Any] | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    #: Top-level keys this build does not know about, preserved verbatim from
    #: disk so a round trip never loses bytes.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "ts": self.ts,
            "device": self.device,
            "actor": self.actor,
            "provenance": self.provenance,
            "payload": self.payload,
        }
        data.update(self.extra)
        return data

    @property
    def instant(self) -> datetime | None:
        return parse_ts_or_none(self.ts)

    @property
    def sort_key(self) -> tuple[datetime, str]:
        """The log's total order: declared time, then ULID.

        ``ts`` is the human-legible authority for when an event was recorded;
        the ULID breaks ties uniquely, so the order does not depend on file
        discovery order, filesystem listing order, or which device wrote what.
        """
        instant = self.instant
        if instant is None:
            # Unparseable timestamps sort first and deterministically rather
            # than crashing a read of a log that already contains one.
            instant = datetime.min.replace(tzinfo=timezone.utc)
        return (instant, self.id)


def from_dict(data: dict[str, Any]) -> Event:
    """Build an Event from a parsed line, preserving unknown keys."""
    if not isinstance(data, dict):
        raise EventValidationError("event must be a JSON object")
    missing = [k for k in ("id", "type", "ts", "device", "actor") if k not in data]
    if missing:
        raise EventValidationError(f"event is missing required keys: {', '.join(missing)}")
    payload = data.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise EventValidationError("payload must be a JSON object")
    provenance = data.get("provenance")
    if provenance is not None and not isinstance(provenance, dict):
        raise EventValidationError("provenance must be a JSON object or null")
    extra = {k: v for k, v in data.items() if k not in ENVELOPE_KEYS}
    return Event(
        id=data["id"],
        type=data["type"],
        ts=data["ts"],
        device=data["device"],
        actor=data["actor"],
        provenance=provenance,
        payload=payload,
        extra=extra,
    )


def validate_for_append(event: Event) -> None:
    """Full strictness, applied only to events this build is about to write."""
    problems: list[str] = []

    if not ulid.is_valid(event.id):
        problems.append(f"id {event.id!r} is not a valid 26-character ULID")
    if event.type not in EVENT_TYPES:
        problems.append(
            f"unknown event type {event.type!r}; known types: "
            f"{', '.join(sorted(EVENT_TYPES))}"
        )
    if event.actor not in ACTORS:
        problems.append(f"actor must be one of {ACTORS}, got {event.actor!r}")
    elif event.type in EVENT_TYPES and EVENT_TYPES[event.type] != event.actor:
        problems.append(
            f"event type {event.type!r} is authored by "
            f"{EVENT_TYPES[event.type]!r}, not {event.actor!r}"
        )
    if not is_canonical_ts(event.ts):
        problems.append(
            f"ts {event.ts!r} must be canonical UTC of the form YYYY-MM-DDTHH:MM:SSZ"
        )
    if not is_valid_device_id(event.device):
        problems.append(f"device {event.device!r} is not a usable device id")
    if event.actor == ACTOR_AGENT and event.provenance is None:
        problems.append(
            "agent-authored events must carry provenance; 'which model produced this "
            "claim' has to be answerable a year later"
        )
    if event.actor == ACTOR_USER and event.provenance is not None:
        problems.append("user-authored events must not carry model provenance")
    if event.extra:
        problems.append(f"unknown top-level keys: {', '.join(sorted(event.extra))}")

    if problems:
        raise EventValidationError("; ".join(problems))


def new(
    type: str,
    device: str,
    actor: str | None = None,
    payload: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    ts: str | None = None,
) -> Event:
    """Mint an event. The actor defaults to the one the registry allows."""
    if actor is None:
        actor = EVENT_TYPES.get(type, ACTOR_USER)
    return Event(
        id=ulid.new(),
        type=type,
        ts=ts or now_ts(),
        device=device,
        actor=actor,
        provenance=provenance,
        payload=payload or {},
    )
