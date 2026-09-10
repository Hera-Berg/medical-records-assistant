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
from . import citations, dates
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
    #: The entity this row is about, where it is about one. Artefact and note
    #: rows carry ``None``: an artefact is not yet about anything — what a
    #: document says is the model's reading of it, and this row is written
    #: before anything has read it. Present so a reader can filter the timeline
    #: to one medication without the filter having to re-derive from the text.
    subject_id: str | None = None
    #: How the footnote introduces whatever ``cite`` points at. Carried on the
    #: row rather than chosen when the page is written, so that one event is
    #: described the same way wherever it is cited: a `claim.corrected` footnoted
    #: as "Your correction" on the medication page and as "Note recorded" on the
    #: timeline is one act of the user's wearing two names.
    cite_description: str = "Note recorded"

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


def _capture_source(event: Event) -> str | None:
    capture = event.payload.get("capture")
    if isinstance(capture, dict):
        source = capture.get("source")
        return source if isinstance(source, str) else None
    return None


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
    # Which entity each claim was actually filed under. A salt variant is
    # aliased onto its base drug in the projection, so a claim whose own subject
    # is `med:metformin-hydrochloride` belongs to the slot for `med:metformin`.
    # Without this the row falls back to the claim's own slug and the timeline
    # shows one medication twice under two spellings — which is the duplication
    # the alias table exists to prevent, and it takes the row's link to a page
    # that does not exist, because an aliased entity gets no stub.
    #
    # The label's own wording is not lost: the settled decision puts that
    # disclosure on the entity page ("the label read X, filed under Y"), where
    # there is room for the sentence, and the artefact behind the citation says
    # it too.
    filed_under: dict[str, str] = {}
    for slot in reconciliation.slots.values():
        for claim in slot.supporting:
            admitted[claim.event_id] = claim
            filed_under[claim.event_id] = slot.subject_id
        for claim in slot.superseded:
            admitted.setdefault(claim.event_id, claim)
            filed_under.setdefault(claim.event_id, slot.subject_id)

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
                    text=(
                        f"{citations.artifact_noun(mime, _capture_source(event))} was "
                        f"added to the record"
                    ),
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
        subject_id = filed_under.get(event_id, claim.subject.id)
        name = (
            entity_names.get(subject_id)
            or entity_names.get(claim.subject.id)
            or claim.subject_literal
        )
        rows.append(
            Row(
                date=placed[0],
                date_kind=placed[1],
                marker=claim.evidence_tier,
                text=f"{name} — {claim.predicate}: {claim.value.literal}",
                cite=claim.cite,
                subject_id=subject_id,
                cite_description=(
                    "Your correction" if claim.is_correction else "Recorded claim"
                ),
                event_id=claim.event_id,
                sort_key=claim.sort_key,
            )
        )

    rows.sort(key=lambda row: row.order, reverse=True)
    return tuple(rows)




def by_month(rows: Iterable[Row]) -> dict[str, list[Row]]:
    """Group rows into their month files, newest row first within each."""
    grouped: dict[str, list[Row]] = {}
    for row in rows:
        grouped.setdefault(row.month, []).append(row)
    for month in grouped:
        grouped[month].sort(key=lambda row: row.order, reverse=True)
    return grouped
