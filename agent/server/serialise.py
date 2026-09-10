"""Projection objects to JSON, with the same restraint the wiki pages use.

The rule this module is built around: **the API says what a wiki page says, and
no more.** Every shape here is derived from :mod:`agent.projection` — the
reconciled slots, the assembled entities, the built timeline — and never from
the raw event stream. That is not a convenience. Reconciliation is what applies
the rejection suppression, and a serialiser that reached past it into the log to
assemble a "full source chain" would put back on screen exactly the content the
user retracted.

Three consequences worth stating outright, because each is a way this could go
quietly wrong:

**Rejected content never appears.** A rejection is a retraction, and printing the
content re-asserts what the user said is not true of them. Rejected claims are
suppressed before any slot is built, so building only from slots is what makes
this hold. There is no ``rejected`` field here to populate.

**Superseded readings do appear**, with their citation and what replaced them.
"What did I correct, and from what" has to be answerable, and a correction whose
original has vanished is unverifiable.

**A pending high-consequence value is not sent.** The entity page states that a
proposed change is waiting and cites its source, without printing the proposed
value beside the current one, because a value printed next to the current one
gets read as current. The API mirrors that. The proposed values belong to the
review inbox, which is phase 7, where the diff is the point of the screen.

Values are rendered as the source wrote them. ``Value.literal`` is what goes on
screen; ``Value.key`` travels with it only so the interface can tell agreement
from coincidence. No number here is ever reconstructed from a float.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ..projection import reconcile
from ..projection.citations import Citation, Citer
from ..projection.claims import Claim
from ..projection.dates import FuzzyDate
from ..projection.entities import Entity
from ..projection.reconcile import ReviewItem, Slot
from ..projection.timeline import DATE_KIND_LABELS, Row

ARTIFACT_URL = "/api/artifact/{short}"


def citation(citer: Citer, key: str, description: str = "Recorded claim") -> dict[str, Any]:
    """One footnote, resolved the same way the wiki resolves it.

    ``url`` is present only for an artefact that is actually in the record. A
    citation the log cannot resolve is still returned, marked unresolved and
    carrying the explanation — a footnote that says the evidence is missing is a
    report, while a dropped footnote is a lie about how well sourced the record
    is.
    """
    resolved = citer.cite(key, description)
    return _citation(resolved)


def _citation(resolved: Citation) -> dict[str, Any]:
    is_artifact = not resolved.key.startswith("ev-")
    return {
        "key": resolved.key,
        "text": resolved.text,
        "target": resolved.target,
        "resolved": resolved.resolved,
        "artifact": resolved.key if is_artifact and resolved.resolved else None,
        "url": (
            ARTIFACT_URL.format(short=resolved.key)
            if is_artifact and resolved.resolved
            else None
        ),
    }


def fuzzy_date(value: FuzzyDate | None) -> dict[str, Any] | None:
    """A date with its fuzz intact.

    ``band`` is the whole point: the timeline renders uncertainty as a band and
    not a point, so the band has to reach the client rather than being inferred
    there from precision — that inference is where a fortnight of slack on a
    month-precision date would quietly be lost.
    """
    if value is None:
        return None
    start, end = value.band
    return {
        "iso": value.iso,
        "precision": value.precision,
        "uncertainty_days": value.uncertainty_days,
        "exact": value.is_exact,
        "render": value.render(),
        "band": {"start": start.isoformat(), "end": end.isoformat()},
        "coercions": list(value.coercions),
    }


def claim(item: Claim, citer: Citer, description: str | None = None) -> dict[str, Any]:
    """One claim, with the four timestamps kept apart.

    Every one of ``occurred_at``, ``artifact_ts``, ``captured_ts`` and
    ``ingested_ts`` is sent as it stands, ``null`` included. Collapsing an
    unknown one into a neighbour here would be invisible on screen and would
    misdate the timeline by however far the two happen to differ.
    """
    described = description or ("Your correction" if item.is_correction else "Recorded claim")
    return {
        "event_id": item.event_id,
        "ts": item.ts,
        "device": item.device,
        "kind": item.kind,
        "is_correction": item.is_correction,
        "subject": item.subject.id,
        # What the label itself said, beside the id it was filed under. A salt
        # variant is aliased onto one drug and gets no page of its own, so this
        # and the citation are the whole of the disclosure.
        "subject_literal": item.subject_literal,
        "salt": item.salt,
        "predicate": item.predicate,
        "value": {
            "literal": item.value.literal,
            "key": item.value.key,
            "fields": dict(item.value.fields),
        },
        "evidence_tier": item.evidence_tier,
        "consequence": item.consequence,
        "declared_consequence": item.declared_consequence,
        "confidence": item.confidence,
        "occurred_at": fuzzy_date(item.occurred_at),
        # Preserved verbatim, never resolved to a date here. "Around Easter" is
        # information the record exists to keep; a year computed for it would be
        # invented.
        "occurred_span": item.occurred_span,
        "artifact_ts": item.artifact_ts,
        "captured_ts": item.captured_ts,
        "ingested_ts": item.ingested_ts,
        "artifact": item.artifact,
        "citation": citation(citer, item.cite, described),
    }


def slot(item: Slot, citer: Citer) -> dict[str, Any]:
    """One reconciled fact, and everything still standing behind it."""
    return {
        "subject_id": item.subject_id,
        "predicate": item.predicate,
        "consequence": item.consequence,
        "resolution": item.resolution,
        "review_state": item.review_state,
        "conflicted": item.is_conflicted,
        "has_value": item.has_value,
        "winner": claim(item.winner, citer) if item.winner is not None else None,
        # Both readings of a conflict, never one picked and never an average.
        "readings": [claim(c, citer) for c in item.readings] if item.is_conflicted else [],
        "superseded": [claim(c, citer) for c in item.superseded],
        "contradicted_by": [claim(c, citer) for c in item.contradicted_by],
        "endorsed": sorted(item.endorsed),
        # Count and sources only. See the module docstring: the proposed value
        # of a gated change is the review inbox's business, not this view's.
        "pending": {
            "count": len(item.pending),
            "citations": [citation(citer, c.cite) for c in item.pending],
        },
    }


def review_item(item: ReviewItem, citer: Citer) -> dict[str, Any]:
    """A queue entry, carrying its summary and its sources but no claim values.

    ``ReviewItem.summary`` is written by the projection to be content-free — a
    withdrawal names its artefact and nothing else, because the point of it is
    that the content must not be reproduced. Passing the summary through while
    dropping ``claims`` keeps that property here.
    """
    keys = [c.cite for c in item.claims] or ([item.cite] if item.cite else [])
    return {
        "kind": item.kind,
        "consequence": item.consequence,
        "subject_id": item.subject_id,
        "predicate": item.predicate,
        "summary": item.summary,
        "citations": [citation(citer, key) for key in keys],
    }


def timeline_row(row: Row, citer: Citer) -> dict[str, Any]:
    """One timeline line, saying which of the four dates placed it.

    ``date_kind`` and its label travel together so the client cannot render a
    row without saying what its date means. A row placed on ingest time and
    presented as when something happened is the failure this field exists to
    prevent.
    """
    return {
        "event_id": row.event_id,
        "subject_id": row.subject_id,
        "marker": row.marker,
        "text": row.text,
        "date": fuzzy_date(row.date),
        "date_kind": row.date_kind,
        "date_label": DATE_KIND_LABELS.get(row.date_kind, ""),
        "heading": row.heading,
        "month": row.month,
        "citation": citation(citer, row.cite, row.cite_description),
    }


def entity_summary(entity: Entity) -> dict[str, Any]:
    """The index row for one entity. No claim values beyond the winning ones."""
    dose = entity.slots.get("dose")
    return {
        "id": entity.id,
        "kind": entity.subject.kind,
        "slug": entity.subject.slug,
        "name": entity.name,
        "status": entity.status,
        "stale": entity.stale,
        "conflicted": bool(entity.conflicts),
        "is_stub": entity.is_stub,
        "merged_into": entity.merged_into,
        "evidence_tier": entity.evidence_tier,
        "dose": (
            dose.winner.value.literal
            if dose is not None and dose.winner is not None
            else None
        ),
        "last_confirmed": entity.last_confirmed.iso if entity.last_confirmed else None,
        "expected_exhaustion": (
            entity.expected_exhaustion.iso if entity.expected_exhaustion else None
        ),
        "started": entity.started.iso if entity.started else None,
        "stop_reported": entity.stop_report.iso if entity.stop_report else None,
        "stop_reported_tier": entity.stop_report.tier if entity.stop_report else None,
        "review_count": len(entity.review),
        "anomaly_count": len(entity.anomalies),
        "sources": list(entity.sources),
        "path": entity.rel_path,
    }


def entity_detail(
    entity: Entity, citer: Citer, page: bytes | None = None
) -> dict[str, Any]:
    """One entity and its full source chain.

    "Full" means every claim reconciliation still holds for this entity: what
    won, what it replaced, what disagrees with it, and what the user endorsed.
    It does not mean every event mentioning the subject — that would reach past
    the layer that suppresses rejected readings.

    ``page`` is the generated markdown, included so a reader can see the exact
    words the folder holds rather than a second rendering of the same facts that
    might not agree with it.
    """
    detail = entity_summary(entity)
    detail.update(
        {
            "slots": [slot(entity.slots[name], citer) for name in sorted(entity.slots)],
            "conflicts": [
                {"predicate": s.predicate, "readings": [claim(c, citer) for c in s.readings]}
                for s in entity.conflicts
            ],
            "review": [review_item(item, citer) for item in entity.review],
            "anomalies": [
                {"text": str(a), "citation": citation(citer, a.cite) if a.cite else None}
                for a in entity.anomalies
            ],
            "salt_names": [
                {
                    "literal": name.literal,
                    "salt": name.salt,
                    "subject_id": name.subject_id,
                    "citation": citation(citer, name.claim.cite),
                }
                for name in entity.salt_names
            ],
            "merged_from": list(entity.merged_from),
            "dispense": _dispense(entity),
            "stop_report": (
                {
                    "tier": entity.stop_report.tier,
                    "when": fuzzy_date(entity.stop_report.when),
                    "citation": citation(citer, entity.stop_report.claim.cite),
                }
                if entity.stop_report is not None
                else None
            ),
            "markdown": page.decode("utf-8") if page is not None else None,
        }
    )
    return detail


def _dispense(entity: Entity) -> dict[str, Any] | None:
    """The supply the script stated, as literal spans.

    Every field is the text the source used. The exhaustion date derived from
    them was computed in Python, never by the model, and lives on the summary.
    """
    supply = entity.dispense
    if supply is None:
        return None
    return {
        "quantity": supply.quantity_literal,
        "frequency": supply.frequency_literal,
        "repeats": supply.repeats_literal,
        "dose_units": supply.dose_units_literal,
        "days_supply": supply.days_supply,
        "unreadable": supply.unreadable,
        # The condition the wiki page uses to decide whether a Supply section
        # exists at all. Sent rather than re-derived, so the page and the screen
        # cannot disagree about whether a source stated a supply: a dose claim's
        # own frequency reaches `Dispense` as a fallback, and a Supply heading
        # built from that says only that there is nothing to say.
        "has_spans": supply.has_spans,
    }


def medication_view(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """The generated current-medications view.

    A view, never a stored file: writing it out would put the medication list in
    two places, and the moment two copies exist one of them is wrong.
    """
    return [row.to_dict() for row in rows]


def anomalies(items: Sequence[str]) -> list[str]:
    return list(items)


def review_counts(items: Sequence[ReviewItem]) -> dict[str, Any]:
    by_tier: dict[str, int] = {}
    for item in items:
        by_tier[item.consequence] = by_tier.get(item.consequence, 0) + 1
    return {
        "total": len(items),
        "by_tier": {tier: by_tier.get(tier, 0) for tier in ("high", "medium", "low")},
        "by_kind": _counted(item.kind for item in items),
    }


def _counted(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def artifact_summary(artifact, mapping: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What the log knows about one stored file.

    The four timestamps again, each explicit. ``captured_ts`` is ``null`` for
    anything that did not come from a live camera or microphone, and the
    interface says "added to the record" rather than "taken" when it is.
    """
    return {
        "short": artifact.short,
        "digest": artifact.digest,
        "path": artifact.rel,
        "mime": artifact.mime,
        "source": artifact.source,
        "ingested_ts": artifact.ingested_ts,
        "captured_ts": artifact.captured_ts,
        "artifact_ts": artifact.artifact_ts,
        "reseen_count": artifact.reseen_count,
        "url": ARTIFACT_URL.format(short=artifact.short),
        **(dict(mapping) if mapping else {}),
    }


#: Review kinds whose entries never carry claim values, for the interface to
#: render differently. Kept here so one list serves the API and the SPA.
CONTENT_FREE_KINDS = (reconcile.WITHDRAWN,)
