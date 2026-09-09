"""``POST /api/rebuild`` — replay the log into ``wiki/``, and say what changed.

The API sketch in ``CLAUDE.md`` describes this as "wipe wiki/ + index, replay
events". The settled decision from phase 3 governs how far the wipe goes: **the
writer deletes only files the previous manifest lists, whose bytes still match.**
Anything else in ``wiki/`` is foreign — a note someone dropped in, a sync
client's conflicted copy, a generated page since edited by hand — and is
reported, never removed. A missing manifest authorises zero deletions.

So this route reports three lists that a "wipe" would have hidden: what was
written, what was left alone as foreign, and what was left alone because it had
been edited. Answering "why is my file still there" with a sentence beats
answering "where did my file go" with an apology.

The index genuinely is wiped, because it genuinely is a cache. It is rebuilt on
the next request that wants it.

Re-extraction (``--reextract`` in ``MODELS.md``, with its diff report of claims
changed, appeared and vanished) is not here. It re-runs the model over every
artefact and presents anything touching a confirmed or corrected claim for
review — which needs the review inbox, and that is phase 7.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ... import projection as projection_mod
from ...errors import HealthAgentError
from ..deps import get_index, get_state
from ..index import Index
from ..state import RecordState

router = APIRouter()


@router.post("/api/rebuild")
def rebuild(
    state: RecordState = Depends(get_state),
    index: Index = Depends(get_index),
) -> dict[str, Any]:
    """Regenerate the derived files from the event log.

    Appends nothing and touches neither ``raw/`` nor ``events/``. Safe to run at
    any time: the log is the only source of truth and this is the function that
    makes that mechanically so.
    """
    with state.lock:
        try:
            report = projection_mod.rebuild(state.vault, as_of=state.now())
        except HealthAgentError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None
        index.discard()
        state.invalidate()
        snapshot = state.snapshot()
        index.ensure(snapshot)

    write = report.write
    return {
        "as_of": snapshot.built_ts,
        "stats": report.projection.stats(),
        "written": list(write.written),
        "unchanged": list(write.unchanged),
        "removed": list(write.removed),
        # Left alone, both of them, and named so the reason is visible.
        "foreign": list(write.foreign),
        "modified": list(write.modified),
        "manifest_missing": write.manifest_missing,
        "anomalies": list(snapshot.anomalies()),
        "problems": list(report.problems),
        "review": {
            tier: len(items)
            for tier, items in sorted(report.projection.review_by_tier.items())
        },
        "note": (
            "wiki/ is regenerated from the log. Files this program did not write "
            "are reported above and were left in place."
        ),
    }
