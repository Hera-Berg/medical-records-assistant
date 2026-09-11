"""The consultation summary: one page, handed over on paper.

This is the artefact the whole project exists to produce. Everything upstream —
the event log, the projection, the review gate, the citations — is in service of
a single sheet a patient can put in front of a GP who has eight minutes.

**No model writes any part of it. Permanently.**

``MODELS.md`` once allowed that the model might write "short connective prose"
inside a template. It does not, and this is settled architecture rather than a
phase 8 scoping decision — a later phase must not "finish" this by wiring the
model back in. Two reasons, and neither improves with a better model:

* Model prose is **the one kind of sentence that cannot carry a citation**. The
  sheet's entire claim on a clinician's attention is that every row names the
  document behind it, and a paragraph that names nothing is the row that breaks
  that claim for all the others.
* It **spends the page budget on text that adds no fact**. One page is a hard
  limit, and connective prose competes for space with a medication.

So the sheet is a pure function of the record: selection in :mod:`.select`, the
one-page limit in :mod:`.budget`, and two renderers that must agree —
:mod:`.markdown` for the archival copy in ``exports/`` and :mod:`.html` for the
sheet that is actually printed.

**A summary is an event, and the document is derived from it.** The event
records the question the patient typed, the reference point, ``as_of``, and the
claims the sheet rested on. Re-rendering projects the log *truncated at that
event* with ``as_of`` pinned, so a sheet handed over in June still reads in
December exactly as it did — a later correction does not rewrite a document
somebody already acted on. See :mod:`.store`.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

from ..projection import dates as dates_mod
from ..projection.citations import Citer
from ..projection.entities import Entity
from ..projection.reconcile import ReviewItem
from . import budget, select
from .model import (
    DATELINE,
    DEMO_WARNING,
    STANDFIRST,
    Line,
    Section,
    Source,
    Summary,
    Waiting,
)

#: The id a sheet carries before it has been prepared. A preview writes nothing
#: and appends nothing, so it has no event to be named after, and naming it
#: something that looks like a ULID would let a preview be mistaken for a
#: prepared sheet in a URL.
PREVIEW_ID = "preview"

#: The window "what changed" covers when there is no previous summary to measure
#: against. Stated on the sheet rather than assumed — a section that reports
#: changes over an unnamed period is asserting a window it never disclosed.
DEFAULT_WINDOW_DAYS = 90


def build(
    entities: Mapping[str, Entity],
    *,
    before: Mapping[str, Entity] | None,
    review: Iterable[ReviewItem],
    citer: Citer,
    prepared: date,
    question: str = "",
    label: str = "",
    since: date | None = None,
    since_ts: str | None = None,
    since_reason: str = "",
    since_note: str = "",
    demo: bool = False,
    summary_id: str = PREVIEW_ID,
) -> Summary:
    """Assemble one sheet from the record, in the order the spec fixes.

    *before* is the record as it stood at the reference point, or ``None`` when
    there is nothing to compare against. It is built by the caller, because
    working out what "then" means requires the whole event log and this function
    deliberately sees only projections.
    """
    heading = f"What changed since {since_reason}" if since_reason else "What changed"
    sections = (
        select.changes(before or {}, entities, citer, heading, since_note)
        if before is not None
        else Section(
            key="changes",
            heading=heading,
            subnote=since_note,
            empty_note=(
                "This is the first summary prepared from my record, so there is "
                "nothing to compare against."
            ),
        ),
        select.medications(entities, citer, prepared),
        select.allergies(entities, citer),
        select.problems(entities, citer),
    )

    fitted = budget.fit(sections, question=question, demo=demo)

    cited: list[str] = []
    sources: list[str] = []
    for section in fitted.sections:
        for line in section.lines:
            for event_id in line.claims:
                if event_id not in cited:
                    cited.append(event_id)
            for source in line.sources:
                if source.artifact and source.artifact not in sources:
                    sources.append(source.artifact)

    return Summary(
        id=summary_id,
        prepared=prepared,
        prepared_words=dates_mod.render_date(prepared),
        label=label,
        question=question.strip(),
        since=since,
        since_ts=since_ts,
        since_reason=since_reason,
        sections=fitted.sections,
        waiting=select.waiting(review),
        demo=demo,
        cited=tuple(sorted(cited)),
        sources=tuple(sorted(sources)),
        overflowed=fitted.overflowed,
        height_mm=fitted.height_mm,
    )


__all__ = [
    "DATELINE",
    "DEMO_WARNING",
    "DEFAULT_WINDOW_DAYS",
    "PREVIEW_ID",
    "STANDFIRST",
    "Line",
    "Section",
    "Source",
    "Summary",
    "Waiting",
    "budget",
    "build",
    "select",
]
