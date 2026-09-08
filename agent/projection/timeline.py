"""The timeline: one file per month, densest screen in the product.

Every row is placed on the best date the record has actually established, and
says **which** date that is. The four timestamps diverge constantly, so a row
placed on ingest time and presented as when something happened is a lie that
nothing about the page would reveal — drag in a photo taken three days ago and
the timeline is wrong by three days for ever.

Rows therefore carry a ``date_kind``:

``occurred``
    ``occurred_at`` — when the thing itself happened. The only kind that needs
    no qualifier in the rendering.
``document``
    ``artifact_ts`` — when the artefact was written. Rendered "document dated".
``captured``
    ``captured_ts`` — when the photo or recording was made.
``recorded``
    ``ingested_ts`` or the event's own time — when it entered the vault, which is
    all that is known. Rendered "recorded", never as when it happened.

Undated material is not dropped and not guessed at: it lands under the month it
was recorded in, labelled ``recorded``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..events.envelope import Event, parse_ts_or_none
from . import dates
from .claims import Claim
from .dates import FuzzyDate
from .reconcile import Reconciliation

OCCURRED = "occurred"
DOCUMENT = "document"
CAPTURED = "captured"
RECORDED = "recorded"

#: How each kind is qualified in the rendered row. ``occurred`` needs nothing.
DATE_KIND_LABELS = {
    OCCURRED: "",
    DOCUMENT: "document dated",
    CAPTURED: "captured",
    RECORDED: "recorded",
}

#: Row markers that are not evidence tiers, because the row is not a claim.
MARKER_ARTEFACT = "artefact"
MARKER_NOTE = "patient-reported"


@dataclass(frozen=True)
class Row:
    """One line on the timeline."""

    date: FuzzyDate
    date_kind: str
    marker: str
    text: str
    cite: str
    event_id: str
    sort_key: tuple[Any, ...]

    @property
    def month(self) -> str:
        return f"{self.date.value.year:04d}-{self.date.value.month:02d}"

    @property
    def heading(self) -> str:
        rendered = self.date.render()
        label = DATE_KIND_LABELS.get(self.date_kind, "")
        return f"{rendered} ({label})" if label else rendered

    @property
    def order(self) -> tuple[Any, ...]:
        return (dates.sort_key(self.date), self.sort_key)


def _date_from_ts(stamp: str | None) -> FuzzyDate | None:
    parsed = parse_ts_or_none(stamp) if stamp else None
    return FuzzyDate(parsed.date()) if parsed else None


def _claim_date(claim: Claim) -> tuple[FuzzyDate, str] | None:
    if claim.occurred_at is not None:
        return claim.occurred_at, OCCURRED
    for stamp, kind in (
        (claim.artifact_ts, DOCUMENT),
        (claim.captured_ts, CAPTURED),
        (claim.ingested_ts, RECORDED),
        (claim.ts, RECORDED),
    ):
        parsed = _date_from_ts(stamp)
        if parsed is not None:
            return parsed, kind
    return None


def _artifact_date(event: Event) -> tuple[FuzzyDate, str] | None:
    payload = event.payload
    for name, kind in (("captured_ts", CAPTURED), ("ingested_ts", RECORDED)):
        parsed = _date_from_ts(payload.get(name))
        if parsed is not None:
            return parsed, kind
    parsed = _date_from_ts(event.ts)
    return (parsed, RECORDED) if parsed else None


def _note_text(event: Event) -> str:
    payload = event.payload
    for name in ("text", "transcript", "note"):
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            collapsed = " ".join(value.split())
            return collapsed if len(collapsed) <= 240 else collapsed[:237] + "…"
    return "A note was recorded"


def build(
    events: Iterable[Event],
    reconciliation: Reconciliation,
    entity_names: Mapping[str, str],
) -> tuple[Row, ...]:
    """Every timeline row, from artefacts, notes and the claims that were admitted."""
    rows: list[Row] = []

    admitted: dict[str, Claim] = {}
    for slot in reconciliation.slots.values():
        for claim in slot.supporting:
            admitted[claim.event_id] = claim
        for claim in slot.superseded:
            admitted.setdefault(claim.event_id, claim)

    for event in events:
        if event.type == "artifact.ingested":
            placed = _artifact_date(event)
            if placed is None:
                continue
            mime = str(event.payload.get("mime") or "")
            short = str(event.payload.get("short") or "")
            if not short:
                continue
            rows.append(
                Row(
                    date=placed[0],
                    date_kind=placed[1],
                    marker=MARKER_ARTEFACT,
                    text=f"{_artifact_noun(mime)} added to the record",
                    cite=short,
                    event_id=event.id,
                    sort_key=event.sort_key,
                )
            )
        elif event.type == "note.recorded":
            parsed = _date_from_ts(event.ts)
            if parsed is None:
                continue
            rows.append(
                Row(
                    date=parsed,
                    date_kind=RECORDED,
                    marker=MARKER_NOTE,
                    text=_note_text(event),
                    cite=f"ev-{event.id}",
                    event_id=event.id,
                    sort_key=event.sort_key,
                )
            )

    for event_id in sorted(admitted):
        claim = admitted[event_id]
        placed = _claim_date(claim)
        if placed is None:
            continue
        name = entity_names.get(claim.subject.id) or claim.subject.slug
        rows.append(
            Row(
                date=placed[0],
                date_kind=placed[1],
                marker=claim.evidence_tier,
                text=f"{name} — {claim.predicate}: {claim.value.literal}",
                cite=claim.cite,
                event_id=claim.event_id,
                sort_key=claim.sort_key,
            )
        )

    rows.sort(key=lambda row: row.order, reverse=True)
    return tuple(rows)


def _artifact_noun(mime: str) -> str:
    if mime.startswith("image/"):
        return "A photograph was"
    if mime == "application/pdf":
        return "A PDF document was"
    if mime.startswith("audio/"):
        return "An audio recording was"
    return "A file was"


def by_month(rows: Iterable[Row]) -> dict[str, list[Row]]:
    """Group rows into their month files, newest row first within each."""
    grouped: dict[str, list[Row]] = {}
    for row in rows:
        grouped.setdefault(row.month, []).append(row)
    for month in grouped:
        grouped[month].sort(key=lambda row: row.order, reverse=True)
    return grouped
