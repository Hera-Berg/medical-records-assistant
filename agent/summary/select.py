"""Which facts reach the sheet, and in what order. All of it code.

``MODELS.md``, permanently: "Consultation summaries stay template-driven.
Structure, section order and selection rules are code. The model writes short
connective prose and never selects what is clinically salient." This project
goes one step further and the model writes nothing at all — see the module
docstring in :mod:`agent.summary`.

So every rule about what a clinician sees is in this file, where it can be read,
argued with and tested. Nothing here consults a model, reads a confidence score,
or ranks one finding as more interesting than another. It reports what the
record holds, in a fixed order, with the sources attached.

**What changed since last visit is a diff of two projections.** The record as it
stands, against the record as it stood at the reference point, both produced by
the same pure reduction the wiki is built from. That is what makes "changed"
mean something checkable rather than something inferred: a value differs, or it
did not exist, and the sheet says which.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

from ..events.envelope import parse_ts_or_none
from ..projection import dates as dates_mod
from ..projection import entities as entities_mod
from ..projection import tiers, views
from ..projection.citations import Citer
from ..projection.claims import Claim
from ..projection.entities import Entity
from ..projection.reconcile import REVIEW_UNREVIEWED, ReviewItem, Slot
from .model import STATUS_WORDS, UNCONFIRMED_WORD, Line, Section, Source, Waiting

#: Predicates a change line never reports. A renamed entity is a filing matter,
#: and the name is already the label of every line it appears on.
_SKIP_PREDICATES = frozenset({"name"})

#: Where the exported sheet sits, relative to the vault root. Every artefact
#: link in the exported HTML is written relative to this, so the sheet opens its
#: own evidence from the folder with no server running.
_EXPORT_PREFIX = "../"


def _label(predicate: str) -> str:
    words = predicate.replace("_", " ").replace(".", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words)


def _document_date(claim: Claim) -> str | None:
    """The date on the document itself, in words. ``artifact_ts`` only.

    Never ``captured_ts`` or ``ingested_ts``. "Prescription · 4 June 2026" is a
    statement about the paper, and the day it was photographed is a different
    fact that happens to be nearby.
    """
    parsed = parse_ts_or_none(claim.artifact_ts) if claim.artifact_ts else None
    return dates_mod.render_date(parsed.date()) if parsed else None


def source_for(claim: Claim, citer: Citer) -> Source:
    """One claim as a source line.

    A correction carries the evidence tier of what it replaced so that it can
    outrank a later re-extraction, but it is something the user typed — so the
    sheet says so rather than attributing the patient's own words to a
    prescriber.
    """
    citation = citer.cite(claim.cite, "Your correction" if claim.is_correction else "Recorded claim")
    tier = "patient-reported" if claim.is_correction else claim.evidence_tier
    return Source(
        tier=tier,
        when=_document_date(claim),
        citation=citation,
        artifact=claim.artifact,
        rel=(_EXPORT_PREFIX + citation.target) if citation.target else None,
    )


def _sources(claims: Iterable[Claim], citer: Citer) -> tuple[Source, ...]:
    """One :class:`Source` per distinct citation, in the order they were used.

    Deduplicated on the citation key rather than on the rendered text. Two
    scripts written on the same day both render "Prescription · 7 August 2026",
    and printing that twice tells the reader nothing while looking like a
    rendering fault — the renderers fold identical text into one span and keep
    both footnotes, which is where the two documents stay visible.
    """
    seen: dict[str, Source] = {}
    for claim in claims:
        source = source_for(claim, citer)
        seen.setdefault(source.citation.key, source)
    return tuple(seen.values())


def _claim_ids(*groups: Iterable[Claim]) -> tuple[str, ...]:
    """The event ids of every claim whose content reaches a row.

    Separate from the citations on purpose. A "was" value is printed for context
    without its own footnote, and the integrity check that withdraws a stored
    sheet after a rejection has to cover every value on the page rather than
    only the footnoted ones.
    """
    seen: list[str] = []
    for group in groups:
        for claim in group:
            if claim.event_id not in seen:
                seen.append(claim.event_id)
    return tuple(seen)


def _fallback_claims(entity: Entity) -> tuple[Claim, ...]:
    """Something to cite for an entity whose slot carries no value.

    An entity exists because a claim put it there, so there is always something.
    A line with no source cannot be constructed at all, which is the point.
    """
    claims = entity.supporting_claims
    if claims:
        return claims[:1]
    for predicate in sorted(entity.slots):
        all_claims = entity.slots[predicate].all_claims
        if all_claims:
            return all_claims[:1]
    return ()


def _is_unreviewed(slot: Slot | None) -> bool:
    return slot is not None and slot.review_state == REVIEW_UNREVIEWED


def _state_words(entity: Entity, slot: Slot | None) -> str | None:
    """The status word and the unconfirmed marker, in one column.

    Both are words, never colours: this page is printed in black and white and
    read by people who do not have the app's palette.
    """
    parts = [STATUS_WORDS.get(entity.status, "")]
    if _is_unreviewed(slot):
        parts.append(UNCONFIRMED_WORD)
    words = [part for part in parts if part]
    return " · ".join(words) if words else None


# -- current medications ----------------------------------------------------


def medications(
    entities: Mapping[str, Entity], citer: Citer, as_of: date
) -> Section:
    """Every medication still on the list, stale and conflicted included.

    The selection is :func:`agent.projection.views.current_medications` and not a
    second rule written here, so the sheet and the screen cannot disagree about
    what the patient is taking. Stale entries stay on it: absence of evidence is
    never evidence of absence, and the entry whose script should have run out is
    the one a clinician most needs to ask about.
    """
    lines: list[Line] = []
    for row in views.current_medications(entities):
        entity = entities[row.id]
        slot = entity.slots.get("dose")
        notes: list[str] = []

        if row.dose_readings:
            value = row.dose_readings[0]
            alternatives = tuple(row.dose_readings[1:])
            claims = slot.readings if slot is not None else ()
        elif row.dose:
            value = row.dose
            alternatives = ()
            claims = (slot.winner,) if slot is not None and slot.winner else ()
        else:
            value = "no dose recorded"
            alternatives = ()
            claims = _fallback_claims(entity)

        if row.stale and row.last_confirmed:
            parsed = dates_mod.parse_iso_date(row.last_confirmed)
            if parsed is not None:
                notes.append(
                    f"last confirmed {entities_mod.elapsed_phrase(parsed, as_of)}"
                )
        if row.stop_reported_tier:
            # The patient is the authority on what they actually take and the
            # prescriber on what was prescribed. A clinician seeing both, with
            # the discrepancy visible, learns something either alone would hide.
            notes.append("I have reported stopping this")

        cited = claims or _fallback_claims(entity)
        lines.append(
            Line(
                label=row.name,
                value=value,
                note="; ".join(notes) or None,
                state=_state_words(entity, slot),
                sources=_sources(cited, citer),
                subject_id=row.id,
                alternatives=alternatives,
                claims=_claim_ids(cited),
            )
        )

    return Section(
        key="medications",
        heading="Current medications",
        lines=tuple(lines),
        empty_note="No medications are recorded in my record.",
        # Never. A missed medication is invisible and is precisely the failure
        # this project exists to prevent, so the one-page limit yields here
        # rather than the list doing so.
        truncatable=False,
    )


# -- allergies --------------------------------------------------------------


def allergies(entities: Mapping[str, Entity], citer: Citer) -> Section:
    """Every allergy the record holds.

    Not one of the four sections the spec names, and on the sheet regardless: a
    handed clinical summary without allergies is not a clinical summary, and
    ``allergy.*`` is high-consequence everywhere else in this system. Truncated
    never, for the same reason the medication list is not.
    """
    lines: list[Line] = []
    for subject_id in sorted(entities):
        entity = entities[subject_id]
        if entity.subject.kind != "allergy" or entity.is_stub:
            continue
        if entity.status == entities_mod.STOPPED:
            continue
        slot = entity.slots.get("reaction")
        if slot is not None and slot.is_conflicted:
            value = slot.readings[0].value.literal
            alternatives = tuple(c.value.literal for c in slot.readings[1:])
            claims = slot.readings
        elif slot is not None and slot.winner is not None:
            value = slot.winner.value.literal
            alternatives = ()
            claims = (slot.winner,)
        else:
            value = "reaction not recorded"
            alternatives = ()
            claims = _fallback_claims(entity)
        lines.append(
            Line(
                label=entity.name,
                value=value,
                state=_state_words(entity, slot),
                sources=_sources(claims, citer),
                subject_id=entity.id,
                alternatives=alternatives,
                claims=_claim_ids(claims),
            )
        )
    return Section(
        key="allergies",
        heading="Allergies",
        lines=tuple(lines),
        empty_note="No allergies are recorded in my record. That is not the same as none.",
        truncatable=False,
    )


# -- active problems --------------------------------------------------------


def problems(entities: Mapping[str, Entity], citer: Citer) -> Section:
    """Problems the record still carries, with their onset where one is known.

    Onset comes from the ``started`` slot alone. The earliest evidence the record
    happens to hold is when the *record* starts, not when the problem did, and
    writing one into the other is the substitution the four-timestamp rule
    exists to prevent — invisible, and wrong by years.
    """
    lines: list[Line] = []
    for subject_id in sorted(entities):
        entity = entities[subject_id]
        if entity.subject.kind != "problem" or entity.is_stub:
            continue
        if entity.status == entities_mod.STOPPED:
            continue
        slot = entity.slots.get("started") or entity.slots.get("onset")
        value = _onset_text(entity, slot)
        claims = (
            (slot.winner,) if slot is not None and slot.winner is not None
            else _fallback_claims(entity)
        )
        lines.append(
            Line(
                label=entity.name,
                value=value,
                state=_state_words(entity, slot),
                sources=_sources(claims, citer),
                subject_id=entity.id,
                claims=_claim_ids(claims),
            )
        )
    return Section(
        key="problems",
        heading="Active problems",
        lines=tuple(lines),
        empty_note="No problems are recorded in my record.",
    )


def _medication_status(
    entity: Entity, winner: Claim, note: str
) -> tuple[str, str]:
    """What a medication's status change actually did to the list.

    Never the raw literal. A patient-reported stop is an explicit user act that
    the tier rule declines to *act* on — the entry stays on the medication list,
    because only a prescriber-issued or lab-issued source can take it off — so a
    change line reading "Status stopped" beside a medication list still carrying
    it is the sheet contradicting itself in front of a clinician.

    Both facts are printed instead, which is the same answer the entity page
    gives: the patient is the authority on what they actually take and the
    prescriber on what was prescribed, and a reader seeing both learns more than
    either alone.
    """
    if entity.status == entities_mod.STOPPED:
        return "Stopped", "no longer on my medication list"
    if entity.stop_report is not None:
        return (
            "I have reported stopping this",
            "it stays on my list — only a prescription or lab result takes one off",
        )
    return f"Status {winner.value.literal}", note


def _onset_text(entity: Entity, slot: Slot | None) -> str:
    """When a problem started, in the most established form the record has.

    Three fallbacks, in order of how much they claim. A parsed date is rendered
    with its own fuzz; a source that said *when* only in words has those words
    printed verbatim — "around Easter" is evidence the record exists to keep,
    and a sheet that dropped it because it would not parse would be losing the
    only thing anyone knows.

    The ``onset`` slot is consulted as well as ``started`` because a problem is
    ordinarily dated by the first and a medication by the second. Reading only
    ``started`` printed "onset not recorded" here while the changes section two
    inches above printed the onset off the same claim — a flat contradiction on
    one page, which is worse than either line alone.
    """
    if entity.started is not None:
        return f"since {entity.started.render()}"
    winner = slot.winner if slot is not None else None
    if winner is None:
        return "onset not recorded"
    text, is_date = _dated_value(winner)
    if not text:
        return "onset not recorded"
    return f"since {text}" if is_date else text


#: Predicates whose value *is* a date, and which therefore render through
#: :func:`_dated_value` wherever they appear. Two sections of one sheet printing
#: the same claim in two different vocabularies is how a reader ends up
#: believing they are two facts.
_DATE_PREDICATES = frozenset({"onset", "started"})


def _dated_value(claim: Claim) -> tuple[str, bool]:
    """A date-valued claim, and whether what came back is actually a date.

    An unresolvable phrase is quoted and labelled rather than printed as though
    it were one. "Since around Easter" on a clinical sheet reads as though the
    record knows which Easter; the record does not, and the review queue is
    where a person turns that into a year. Discarding the phrase would lose
    evidence the record exists to keep, so it is printed — in quotation marks,
    which is what the entity page does with it.

    The flag is what lets each caller supply its own preposition. "Started since
    around November 2024" is the sentence you get when a caller assumes one,
    and it is the kind of wrongness that makes a record look unread.
    """
    if claim.occurred_at is not None:
        return claim.occurred_at.render(), True
    if claim.occurred_span:
        return f"dated only as \u201c{claim.occurred_span}\u201d", False
    return claim.value.literal.strip(), False


# -- what changed -----------------------------------------------------------


def _winners(entities: Mapping[str, Entity]) -> dict[tuple[str, str], Claim]:
    found: dict[tuple[str, str], Claim] = {}
    for subject_id, entity in entities.items():
        if entity.is_stub:
            continue
        for predicate, slot in entity.slots.items():
            if predicate in _SKIP_PREDICATES:
                continue
            if slot.winner is not None:
                found[(subject_id, predicate)] = slot.winner
    return found


def _conflicted(entities: Mapping[str, Entity]) -> dict[tuple[str, str], Slot]:
    found: dict[tuple[str, str], Slot] = {}
    for subject_id, entity in entities.items():
        if entity.is_stub:
            continue
        for predicate, slot in entity.slots.items():
            if predicate not in _SKIP_PREDICATES and slot.is_conflicted:
                found[(subject_id, predicate)] = slot
    return found


def changes(
    before: Mapping[str, Entity],
    now: Mapping[str, Entity],
    citer: Citer,
    heading: str,
    subnote: str = "",
) -> Section:
    """What the record says now that it did not say at the reference point.

    Ordered by consequence — a dose change before a practitioner's job title —
    and then by subject and predicate, so the same pair of projections always
    produces the same sheet.

    Nothing here characterises a change. "Dose 5mg daily, was 4mg daily" is the
    whole of what a row says; whether that matters is the conversation the sheet
    exists to start, not a judgement this code is allowed to make.
    """
    before_winners = _winners(before)
    now_winners = _winners(now)
    now_conflicts = _conflicted(now)
    before_conflicts = _conflicted(before)

    rows: list[tuple[tuple, Line]] = []
    # A medication that stopped has one thing worth reporting and it is that it
    # stopped. Printing "Amitriptyline — dose 10mg at night" beside
    # "Amitriptyline — stopped" invites the first line to be read as current, on
    # the one page where that mistake is most expensive.
    subsumed = {
        subject
        for subject, predicate in set(now_winners) | set(now_conflicts)
        if predicate == "status" and (subject, "status") not in before_winners
    }

    for key in sorted(set(now_winners) | set(now_conflicts)):
        subject_id, predicate = key
        if subject_id in subsumed and predicate != "status":
            continue
        entity = now[subject_id]
        was = before_winners.get(key)
        new_entity = subject_id not in before or before[subject_id].is_stub

        if key in now_conflicts:
            if key in before_conflicts:
                continue
            slot = now_conflicts[key]
            readings = [c.value.literal for c in slot.readings]
            # The state word is "Sources disagree" and the two readings are
            # printed with "or" between them; a sentence repeating that in the
            # note would be the row saying one thing three times on a page whose
            # scarcest resource is lines.
            notes = [f"was {was.value.literal}"] if was is not None else []
            line = Line(
                label=entity.name,
                # The predicate leads and the readings follow it, both of them,
                # joined by "or" like every other unresolved slot on the sheet.
                # Putting "sources disagree" in the value slot instead leaves the
                # readings dangling off it as though they were a third and fourth
                # possibility.
                value=f"{_label(predicate)} {readings[0]}",
                note="; ".join(notes) or None,
                state=STATUS_WORDS.get("conflicted"),
                sources=_sources(slot.readings, citer),
                subject_id=subject_id,
                alternatives=tuple(readings[1:]),
                claims=_claim_ids(slot.readings, (was,) if was is not None else ()),
            )
        else:
            winner = now_winners[key]
            if was is not None and was.value.agrees_with(winner.value):
                continue
            if was is not None:
                note = f"was {was.value.literal}"
            elif new_entity:
                note = "new in my record"
            else:
                note = "newly recorded"
            if predicate == "status" and entity.subject.kind == "med":
                value, note = _medication_status(entity, winner, note)
            elif predicate in _DATE_PREDICATES:
                # The predicate is the preposition here: "Started around
                # November 2024" needs no "since" in front of the date.
                value = f"{_label(predicate)} {_dated_value(winner)[0]}"
            else:
                value = f"{_label(predicate)} {winner.value.literal}"
            line = Line(
                label=entity.name,
                value=value,
                note=note,
                # The slot's own state, never the entity's. "Dose 10mg at night
                # … Stopped" reads as a dose that was stopped, on a line whose
                # subject is the dose; the entity's status has its own change
                # line when it changed, and says so in the value where it
                # belongs.
                state=(
                    UNCONFIRMED_WORD
                    if _is_unreviewed(entity.slots.get(predicate))
                    else None
                ),
                sources=_sources((winner,), citer),
                subject_id=subject_id,
                claims=_claim_ids((winner,), (was,) if was is not None else ()),
            )

        consequence = tiers.consequence_for(entity.subject.kind, predicate)
        if consequence == tiers.LOW:
            # A practitioner's clinic phone number changing is a change to the
            # record and is not a change to report to a clinician on one page.
            # The selection rule is the page's whole job — see the module
            # docstring — and this is one, stated here rather than left to the
            # budget to arrive at by accident.
            continue
        rows.append(((-tiers.consequence_rank(consequence), subject_id, predicate), line))

    rows.sort(key=lambda pair: pair[0])
    return Section(
        key="changes",
        heading=heading,
        subnote=subnote,
        lines=tuple(line for _, line in rows),
        empty_note="Nothing in my record has changed over this period.",
    )


# -- what is being withheld -------------------------------------------------


def waiting(review: Iterable[ReviewItem]) -> Waiting:
    """What the review queue is holding back, and whether it is load-bearing.

    The count alone does not tell a clinician whether the medication list in
    front of them might be short one. The high-consequence subset and the kinds
    it touches do, which is why they travel separately — and why the kinds are
    named and the subjects are not. See :class:`agent.summary.model.Waiting`.
    """
    items = list(review)
    high = [item for item in items if item.consequence == tiers.HIGH]
    kinds: list[str] = []
    for item in high:
        kind = item.subject_id.partition(":")[0]
        if kind not in kinds:
            kinds.append(kind)
    return Waiting(total=len(items), high=len(high), kinds=tuple(sorted(kinds)))


__all__ = [
    "Section",
    "allergies",
    "changes",
    "medications",
    "problems",
    "source_for",
    "waiting",
]
