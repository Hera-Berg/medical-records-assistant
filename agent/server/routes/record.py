"""The read routes: the timeline, the entity index, and one entity's chain.

All three answer from :func:`agent.projection.project` — the same pure reduction
the CLI runs, held in memory and recomputed only when the log changes or the day
does. None of them writes anything. A ``GET`` that rewrote ``wiki/`` would churn
the user's synced folder every time a page was opened.

The index in ``.agent/index.sqlite`` accelerates the range query and nothing
more. Where it declines to answer, the same question is answered by walking the
projection, and the two produce identical output — that is what makes the file
disposable rather than merely regenerable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ...projection import subjects
from ...projection.timeline import by_month
from .. import serialise
from ..deps import get_index, get_state
from ..index import Index, in_range
from ..state import RecordState

router = APIRouter()

#: Rows returned when the caller does not say. Large enough that the first
#: screen of a dense timeline never paginates, small enough that a lifetime
#: record does not arrive in one response.
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000


@router.get("/api/timeline")
def timeline(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    subject: str | None = Query(None),
    tier: list[str] | None = Query(None),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    state: RecordState = Depends(get_state),
    index: Index = Depends(get_index),
) -> dict[str, Any]:
    """The merged event view, newest first.

    ``from`` and ``to`` match on the date **band**, not the point date. A row
    dated "around June 2026 (±15 days)" belongs in a June query: testing the
    point alone would drop precisely the rows whose dating is least certain,
    which are the ones someone scanning a date range most needs to see.
    """
    snapshot = state.snapshot()
    rows = snapshot.projection.rows

    keys = None
    if index.ensure(snapshot):
        keys = index.row_keys(
            start=from_, end=to, subject_id=subject, markers=tuple(tier or ())
        )
    if keys is not None:
        selected = [rows[key.ord] for key in keys if key.ord < len(rows)]
    else:
        markers = set(tier or ())
        selected = [
            row
            for row in rows
            if in_range(row, from_, to)
            and (subject is None or row.subject_id == subject)
            and (not markers or row.marker in markers)
        ]

    window = selected[offset : offset + limit]
    return {
        "total": len(selected),
        "offset": offset,
        "limit": limit,
        "rows": [serialise.timeline_row(row, snapshot.citer) for row in window],
        "months": sorted(by_month(selected), reverse=True),
        "as_of": snapshot.built_ts,
    }


@router.get("/api/wiki")
def wiki_index(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    """Every entity, grouped by kind, plus the generated medications view.

    "Current medications" is a view and never a file — writing it out would put
    the medication list in two places, and the moment two copies exist one of
    them is wrong. It arrives here computed from the same entities listed below
    it, so the two cannot disagree.
    """
    snapshot = state.snapshot()
    entities = snapshot.projection.entities

    grouped: dict[str, list[dict[str, Any]]] = {}
    for subject_id in sorted(entities):
        summary = serialise.entity_summary(entities[subject_id], snapshot.as_of.date())
        grouped.setdefault(summary["kind"], []).append(summary)

    return {
        "kinds": {
            kind: grouped.get(kind, [])
            for kind in sorted(subjects.KIND_DIRS)
        },
        "current_medications": serialise.medication_view(
            snapshot.projection.current_medications, snapshot.as_of.date()
        ),
        "review": serialise.review_counts(snapshot.projection.review),
        "anomalies": snapshot.anomaly_count,
        "as_of": snapshot.built_ts,
    }


@router.get("/api/wiki/{entity_id:path}")
def wiki_entity(
    entity_id: str, state: RecordState = Depends(get_state)
) -> dict[str, Any]:
    """One entity and the claims still standing behind it.

    The identifier is parsed by :mod:`agent.projection.subjects` before anything
    else happens. That parser is the project's path-safety boundary: it refuses
    ``med:../../etc/passwd`` outright rather than cleaning it into something
    plausible, and a subject that cannot parse is not a page that can exist.

    The chain is built from the reconciled slots, never from the raw event
    stream. Reconciliation is what suppresses rejected readings, so reaching
    past it here would put retracted content back on screen — including in a
    document that gets printed and handed to a clinician.
    """
    subject = subjects.parse(entity_id)
    if subject is None:
        raise HTTPException(
            status_code=400, detail=subjects.describe_rejection(entity_id)
        )

    snapshot = state.snapshot()
    entity = snapshot.projection.entities.get(subject.id)
    if entity is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"nothing in this record is filed under {subject.id}. It may never "
                f"have been mentioned, or every reading of it may have been rejected."
            ),
        )

    page = snapshot.projection.files.get(entity.rel_path)
    detail = serialise.entity_detail(
        entity, snapshot.citer, page, as_of=snapshot.as_of.date()
    )
    detail["timeline"] = [
        serialise.timeline_row(row, snapshot.citer)
        for row in snapshot.projection.rows
        if row.subject_id == subject.id
    ]
    detail["as_of"] = snapshot.built_ts
    return detail


@router.get("/api/review")
def review(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    """What is waiting on a person, grouped by consequence tier.

    Phase 5 reports this; phase 7 acts on it. There is deliberately no route
    here that confirms, corrects or rejects anything — the consequence gate and
    the correction flow are that phase's work, and a half-built one would be a
    way for a high-consequence claim to reach the wiki without a deliberate tap.

    The entries carry their summaries and their sources but no claim values, for
    the same reason the entity page does not print a pending value beside a
    current one: it gets read as current.
    """
    snapshot = state.snapshot()
    items = snapshot.projection.review
    grouped: dict[str, list[dict[str, Any]]] = {"high": [], "medium": [], "low": []}
    for item in items:
        grouped.setdefault(item.consequence, []).append(
            serialise.review_item(item, snapshot.citer)
        )
    return {
        "counts": serialise.review_counts(items),
        "tiers": grouped,
        "actionable": False,
        "note": (
            "Reviewing is phase 7. Nothing here can be confirmed, corrected or "
            "rejected yet — use `health-agent` on the terminal in the meantime."
        ),
        "as_of": snapshot.built_ts,
    }


@router.get("/api/medications")
def medications(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    """The generated current-medications view on its own.

    Stale entries are on it, deliberately. Absence of evidence is not evidence
    of absence, and a medication whose script should have run out is exactly the
    one a clinician needs to ask about.
    """
    snapshot = state.snapshot()
    return {
        "rows": serialise.medication_view(
            snapshot.projection.current_medications, snapshot.as_of.date()
        ),
        "as_of": snapshot.built_ts,
    }


@router.get("/api/timeline/months")
def timeline_months(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    """Which months have rows, and how many. Drives the range picker."""
    snapshot = state.snapshot()
    grouped = by_month(snapshot.projection.rows)
    return {
        "months": [
            {"month": month, "rows": len(grouped[month])}
            for month in sorted(grouped, reverse=True)
        ],
        "as_of": snapshot.built_ts,
    }
