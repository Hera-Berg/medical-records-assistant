"""Composing the actual pages: one file per entity, one file per month.

Per-entity files rather than one big list, because that gives clean diffs and
per-fact citation granularity. "Current medications" is a generated view, not a
stored file — see :mod:`.views`.

The prose here is deliberately flat. This is a medical record: it reports what a
source said, names the source, and stops. It does not characterise a trend, rank
a risk, or suggest what anything means.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from . import dates, entities as entities_mod, reconcile, timeline as timeline_mod
from .citations import Citation, Citer
from .entities import Entity
from .reconcile import Slot
from .render import Document, Sentence

#: Predicates whose value is already carried by a dedicated frontmatter field.
_FRONTMATTER_SKIP = frozenset({"name", "status"})


def _label(predicate: str) -> str:
    words = predicate.replace("_", " ").replace(".", " ").split()
    return " ".join(w[0].upper() + w[1:] if w else w for w in words[:1] + words[1:])


def _claim_phrase(claim, citer: Citer, extra: str | None = None) -> tuple[str, Citation]:
    """"5mg daily (prescriber-issued, 4 June 2026)" and the footnote for it.

    The value is always the literal span the source used. The parenthesis says
    where it came from and when, so the tier is visible on every line without a
    reader having to hold the page's structure in their head.
    """
    citation = citer.cite(claim.cite, _correction_description(claim))
    qualifiers = [claim.evidence_tier]
    when = claim.occurred_at
    if when is not None:
        qualifiers.append(when.render())
    if extra:
        qualifiers.append(extra)
    return f"{claim.value.literal} ({', '.join(qualifiers)})", citation


def _correction_description(claim) -> str:
    return "Your correction" if claim.is_correction else "Recorded claim"


def _slot_bullet(document: Document, slot: Slot, citer: Citer) -> None:
    if slot.winner is not None:
        extra = (
            "unreviewed" if slot.review_state == reconcile.REVIEW_UNREVIEWED else None
        )
        phrase, citation = _claim_phrase(slot.winner, citer, extra)
        document.bullet(Sentence(f"**{_label(slot.predicate)}** — {phrase}", [citation]))
        return
    citations = [citer.cite(c.cite, _correction_description(c)) for c in slot.readings]
    document.bullet(
        Sentence(
            f"**{_label(slot.predicate)}** — not resolved: "
            f"{len(slot.readings)} sources of equal standing disagree",
            citations,
        )
    )


def _supply_section(document: Document, entity: Entity, citer: Citer, as_of_date) -> None:
    supply = entity.dispense
    claim = entity.dispense_claim
    if supply is None or claim is None:
        return
    citation = citer.cite(claim.cite, _correction_description(claim))
    document.heading("Supply")
    sentences = []

    when = claim.occurred_at
    if when is not None:
        sentences.append(
            Sentence(
                f"The most recent {claim.evidence_tier} source for this is dated "
                f"{when.render()}",
                [citation],
            )
        )

    spans = supply.describe()
    days = supply.days_supply
    if days is not None and spans:
        sentences.append(
            Sentence(f"It records {spans}, which is {days} days of supply", [citation])
        )
    elif supply.unreadable:
        sentences.append(
            Sentence(
                f"No expected exhaustion date is recorded here because "
                f"{supply.unreadable}",
                [citation],
            )
        )

    if entity.expected_exhaustion is not None:
        sentences.append(
            Sentence(
                f"On that reading the supply runs out "
                f"{entity.expected_exhaustion.render()}",
                [citation],
            )
        )
    if entity.stale and entity.last_confirmed is not None:
        elapsed = entities_mod.elapsed_phrase(entity.last_confirmed.value, as_of_date)
        sentences.append(
            Sentence(
                f"Nothing since has confirmed it, so this entry is marked stale — last "
                f"confirmed {elapsed}",
                [citation],
            )
        )
    document.paragraph(*sentences)


def _conflicts_section(document: Document, entity: Entity, citer: Citer) -> None:
    conflicts = entity.conflicts
    if not conflicts:
        return
    document.heading("Conflicting sources")
    for slot in conflicts:
        citations = [citer.cite(c.cite, _correction_description(c)) for c in slot.readings]
        document.paragraph(
            Sentence(
                f"{len(slot.readings)} sources of equal standing disagree about the "
                f"{_label(slot.predicate).lower()}, and neither has been chosen",
                citations,
            )
        )
        document.blank()
        for claim in slot.readings:
            phrase, citation = _claim_phrase(claim, citer)
            document.bullet(Sentence(phrase, [citation]))


def _review_section(document: Document, entity: Entity, citer: Citer) -> None:
    contradictions = entity.contradictions
    pending = [
        item for item in entity.review if item.kind == "awaiting-confirmation"
    ]
    if not contradictions and not pending:
        return
    document.heading("Needs review")

    for slot in contradictions:
        winner = slot.winner
        winner_citation = citer.cite(winner.cite, _correction_description(winner))
        disagree_citations = [
            citer.cite(c.cite, _correction_description(c)) for c in slot.contradicted_by
        ]
        document.paragraph(
            Sentence(
                f"A later reading disagrees with your correction of the "
                f"{_label(slot.predicate).lower()}; your correction stands until you "
                f"decide otherwise",
                [winner_citation] + disagree_citations,
            )
        )
        document.blank()
        for claim in slot.contradicted_by:
            phrase, citation = _claim_phrase(claim, citer)
            document.bullet(Sentence(f"read instead as {phrase}", [citation]))

    for item in pending:
        # Deliberately does not state the proposed value. A high-consequence
        # change has not been accepted, and a page that prints it beside the
        # current one invites it to be read as current.
        citations = [citer.cite(c.cite, _correction_description(c)) for c in item.claims]
        document.paragraph(
            Sentence(
                f"A proposed change to the {_label(item.predicate).lower()} is waiting "
                f"for you to review it in the inbox",
                citations,
            )
        )


def _history_section(document: Document, entity: Entity, citer: Citer) -> None:
    rows: list[tuple[str, Citation]] = []
    for predicate in sorted(entity.slots):
        slot = entity.slots[predicate]
        for claim in slot.superseded:
            phrase, citation = _claim_phrase(claim, citer)
            rows.append((f"**{_label(predicate)}** — {phrase}", citation))
    if not rows:
        return
    document.heading("Earlier readings")
    for text, citation in rows:
        document.bullet(Sentence(text, [citation]))


def entity_page(entity: Entity, citer: Citer, as_of_date) -> bytes:
    """Render one entity file."""
    document = Document()
    document.field_("id", entity.id)
    document.field_("name", entity.name)

    if entity.is_stub:
        document.field_("merged_into", entity.merged_into)
        # Cite the merge decision itself. A merge is an event the user made, not
        # something an artefact said, and it is reversible — the footnote points
        # at the line in the log that can be reverted.
        citation = citer.cite(f"ev-{entity.merge_event}", "Your merge decision")
        document.paragraph(
            Sentence(
                f"This entry was merged into `{entity.merged_into}` and is kept so that "
                f"older citations and filenames still resolve",
                [citation],
            )
        )
        return document.render()

    document.field_("status", entity.status)
    if entity.stale:
        document.field_("stale", True)

    for predicate in sorted(entity.slots):
        if predicate in _FRONTMATTER_SKIP:
            continue
        slot = entity.slots[predicate]
        if slot.winner is not None:
            document.field_(predicate, slot.winner.value.literal)

    if entity.subject.kind == "med":
        document.field_("started", entity.started.iso if entity.started else None)
    document.field_(
        "last_confirmed", entity.last_confirmed.iso if entity.last_confirmed else None
    )
    if entity.subject.kind == "med":
        document.field_(
            "expected_exhaustion",
            entity.expected_exhaustion.iso if entity.expected_exhaustion else None,
        )
    if entity.evidence_tier:
        document.field_("evidence_tier", entity.evidence_tier)

    unreviewed = any(
        slot.review_state == reconcile.REVIEW_UNREVIEWED for slot in entity.slots.values()
    )
    document.field_("reviewed", "unreviewed" if unreviewed else "confirmed")

    if entity.conflicts:
        document.field_(
            "conflicts",
            [
                {
                    "predicate": slot.predicate,
                    "readings": [c.value.literal for c in slot.readings],
                }
                for slot in entity.conflicts
            ],
        )
    if entity.merged_from:
        document.field_("merged_from", list(entity.merged_from))
    document.field_("sources", list(entity.sources))

    document.heading("Current")
    for predicate in sorted(entity.slots):
        if predicate == "name":
            continue
        _slot_bullet(document, entity.slots[predicate], citer)

    if entity.subject.kind == "med":
        _supply_section(document, entity, citer, as_of_date)
    _conflicts_section(document, entity, citer)
    _review_section(document, entity, citer)
    _history_section(document, entity, citer)

    return document.render()


def timeline_page(month: str, rows: Sequence[timeline_mod.Row], citer: Citer) -> bytes:
    """Render one month of the timeline, newest first."""
    document = Document()
    document.field_("month", month)
    year, _, month_number = month.partition("-")
    document.field_("title", dates.render_month(int(year), int(month_number)))
    document.field_("entries", len(rows))

    current_heading: str | None = None
    for row in rows:
        if row.heading != current_heading:
            document.heading(row.heading, level=2)
            current_heading = row.heading
        citation = citer.cite(row.cite, "Note recorded")
        document.bullet(Sentence(f"**{row.marker}** — {row.text}", [citation]))
    return document.render()


def month_range(months: Iterable[str]) -> list[str]:
    return sorted(set(months))
