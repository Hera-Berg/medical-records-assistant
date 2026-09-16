"""Finding what a question is about, deterministically, in code.

This module is the reason the feature is allowed to exist. CLAUDE.md: "If this
ever starts wanting a tool loop, that is a signal the retrieval layer is too
weak, not that the system needs a planner." Everything here is a lookup or a
filter over the projection, and every one of them is reproducible: the same
question against the same snapshot selects the same passages, in the same order,
on any machine.

**Nothing is read from the event log.** Retrieval works from the projection —
reconciled slots, assembled entities, built timeline rows — for exactly the
reason :mod:`agent.server.serialise` gives: reconciliation is what applies the
rejection suppression, and anything that reached past it into the raw stream
would put back in front of the user the content they retracted. There is no code
path here that touches a ``claim.proposed`` payload or an ``extraction.completed``
event.

**And then a second suppression, over free text.** A rejection retracts a claim,
not a recording, so a transcript's own words can still carry the phrasing behind
a claim the user rejected — legitimately, on the timeline, where the record shows
everything it holds. An answer is a different object: it gets read aloud in a
consulting room and pasted into a message. So a passage carrying a rejected
reading's own wording is withheld from the context and therefore from the answer,
silently, because naming it would point straight at it.

That withholding is keyed on ``(wording, artefact)``, exactly as the projection's
own rejection suppression is, and for the same settled reason: re-reading the
same recording is the case the user decided, while **a different document saying
the same thing is new evidence and theirs to decide again**. Withholding on
wording alone was the first implementation and it was wrong — it hid a second,
confirmed document because an earlier one had been rejected in the same words.

**Budgets are structural, not advisory.** Every facet has a cap, the passage list
has a cap, and the prompt has a token cap enforced in :mod:`agent.query.context`.
A question that names a whole shelf of the record retrieves a bounded slice of
it, never the whole wiki.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Mapping, Sequence

from ..projection import Projection
from ..projection.citations import Citation, Citer
from ..projection.claims import Claim
from ..projection.entities import Entity
from ..projection.reconcile import REJECTED, Slot
from ..projection.timeline import Row
from . import classify as classify_mod
from .classify import Question, Window
from .text import contains_phrase, fold
from .vocab import Vocabulary

#: Per-facet caps. Generous enough that a real question is answered in full,
#: small enough that no question can drag the whole record into a prompt.
MAX_ENTITIES = 12
MAX_ROWS = 40
MAX_PASSAGES = 60

#: A rejected literal shorter than this is not used to withhold anything. Short
#: values ("rash", "5mg") occur legitimately all over a record, and suppressing
#: every line containing one would quietly blank passages that have nothing to
#: do with the rejection.
MIN_SUPPRESSED_LITERAL = 5

# Passage kinds. What sort of thing the model is being shown.
FACT = "fact"
EARLIER = "earlier"
READING = "reading"
LIFECYCLE = "lifecycle"
ROW = "row"


@dataclass(frozen=True)
class Passage:
    """One piece of the record, with the footnote it will be answered under.

    A passage with no citation cannot exist: the constructor of every passage
    below takes the claim or row it came from, and a citation key is what it
    takes from it. That is what makes "every sentence carries a citation"
    enforceable rather than requested — the model can only cite what it was
    shown, and it was only shown things that carry a citation.
    """

    key: str
    kind: str
    subject_id: str | None
    title: str
    text: str
    #: The evidence tier this rests on, kept as its own field rather than folded
    #: into ``text``. The prompt wants the record's own vocabulary and the screen
    #: wants the patient's — "Prescription", not "prescriber-issued" — and one
    #: string cannot be both without the interface parsing prose back apart.
    tier: str | None
    #: True where the value is something the person typed themselves. A
    #: correction carries the tier of what it replaced so it can outrank a later
    #: re-reading, and printing that tier unqualified would attribute the
    #: patient's own typing to a prescriber.
    corrected: bool
    #: The date this is dated to, already rendered. Empty where nothing
    #: established one — never filled in from a neighbouring timestamp.
    when: str
    citation: Citation
    #: Lower sorts earlier and survives trimming longer.
    rank: int
    #: ``(group, slot, position)``. One shape for every passage, because two
    #: shapes cannot be sorted against each other and the failure is a crash
    #: in the middle of answering a question rather than a wrong order.
    order: tuple[str, str, int]
    #: Why this passage was retrieved, for the "what was found" view.
    facet: str

    @property
    def provenance(self) -> str:
        """"(prescriber-issued, 4 June 2026)", for the prompt."""
        parts = ["you corrected this" if self.corrected else (self.tier or "")]
        if self.when:
            parts.append(self.when)
        joined = ", ".join(part for part in parts if part)
        return f" ({joined})" if joined else ""

    @property
    def line(self) -> str:
        """The passage as the prompt shows it."""
        return f"[{self.key}] {self.title}: {self.text}{self.provenance}"


@dataclass(frozen=True)
class Retrieval:
    """What was found, and everything the answer is allowed to rest on."""

    passages: tuple[Passage, ...] = ()
    question: Question | None = None
    #: How many passages the literal-term facet matched. Counted in code and
    #: rendered in code — the model never produces a number.
    term_hits: int = 0
    #: Set when the one bounded expansion ran, and what it searched for.
    expanded_terms: tuple[str, ...] = ()
    withheld: int = field(default=0, repr=False)

    @property
    def is_empty(self) -> bool:
        return not self.passages

    @property
    def keys(self) -> frozenset[str]:
        """The citation keys an answer may use. Anything else is dropped."""
        return frozenset(passage.key for passage in self.passages)

    def citation_for(self, key: str) -> Citation | None:
        for passage in self.passages:
            if passage.key == key:
                return passage.citation
        return None

    def sources(self) -> tuple[Citation, ...]:
        """One citation per distinct artefact, in the order they were found."""
        seen: dict[str, Citation] = {}
        for passage in self.passages:
            seen.setdefault(passage.key, passage.citation)
        return tuple(seen.values())


# -- withholding rejected content --------------------------------------------


def rejected_wordings(projection: Projection) -> frozenset[tuple[str, str]]:
    """``(wording, artefact)`` for every reading the user rejected.

    Read from the reconciliation's own admissions, so this is the same set of
    claims the projection refused to admit rather than a second reading of the
    log that could disagree with it.

    The artefact travels with the wording because the suppression key does. A
    rejection is a decision about *this reading of this document*; another
    document saying the same thing has not been decided and must not be hidden
    by this.
    """
    reconciliation = projection.reconciliation
    if reconciliation is None:
        return frozenset()
    found: set[tuple[str, str]] = set()
    for admission in reconciliation.admissions:
        if admission.state != REJECTED:
            continue
        where = admission.claim.cite
        literal = fold(admission.claim.value.literal)
        if len(literal) >= MIN_SUPPRESSED_LITERAL:
            found.add((literal, where))
        # The subject's own wording too: a rejected `problem:alcohol-dependence`
        # names itself in its slug as well as in its value, and a question aimed
        # at the subject is the obvious way to go looking for it.
        subject = fold(admission.claim.subject_literal)
        if len(subject) >= MIN_SUPPRESSED_LITERAL:
            found.add((subject, where))
    return frozenset(found)


def _carries_rejected(passage: Passage, wordings: frozenset[tuple[str, str]]) -> bool:
    text = f"{passage.title} {passage.text}"
    return any(
        passage.key == where and contains_phrase(text, wording)
        for wording, where in wordings
    )


# -- rendering one piece of the record ---------------------------------------


def _when(claim: Claim) -> str:
    if claim.occurred_at is not None:
        return claim.occurred_at.render()
    if claim.occurred_span:
        return f"dated only as “{claim.occurred_span}”"
    return ""


def _title(entity: Entity, predicate: str) -> str:
    return f"{entity.name} — {predicate.replace('_', ' ')}"


def _passage(
    *,
    entity: Entity,
    claim: Claim,
    citer: Citer,
    kind: str,
    facet: str,
    rank: int,
    suffix: str = "",
    description: str = "Recorded claim",
) -> Passage:
    return Passage(
        key=claim.cite,
        kind=kind,
        subject_id=entity.id,
        title=_title(entity, claim.predicate),
        text=f"{claim.value.literal}{suffix}",
        tier=claim.evidence_tier,
        corrected=claim.is_correction,
        when=_when(claim),
        citation=citer.cite(claim.cite, description),
        rank=rank,
        order=(entity.id, claim.predicate, 0),
        facet=facet,
    )


def _entity_passages(
    entity: Entity, citer: Citer, facet: str, rank: int
) -> list[Passage]:
    """One entity as the model sees it: its facts, its state, its history."""
    found: list[Passage] = []
    for predicate in sorted(entity.slots):
        slot = entity.slots[predicate]
        if slot.is_conflicted:
            for reading in slot.readings:
                found.append(
                    _passage(
                        entity=entity,
                        claim=reading,
                        citer=citer,
                        kind=READING,
                        facet=facet,
                        rank=rank,
                        suffix=" — one of two readings that disagree; neither has"
                        " been chosen",
                    )
                )
            continue
        if slot.winner is not None:
            found.append(
                _passage(
                    entity=entity,
                    claim=slot.winner,
                    citer=citer,
                    kind=FACT,
                    facet=facet,
                    rank=rank,
                    description="Your correction" if slot.winner.is_correction else "Recorded claim",
                )
            )
        for earlier in slot.superseded:
            found.append(
                _passage(
                    entity=entity,
                    claim=earlier,
                    citer=citer,
                    kind=EARLIER,
                    facet=facet,
                    rank=rank + 2,
                    suffix=" — an earlier reading, since replaced",
                )
            )
    found.extend(_lifecycle_passages(entity, citer, facet, rank))
    return found


def _lifecycle_passages(
    entity: Entity, citer: Citer, facet: str, rank: int
) -> list[Passage]:
    """Status, staleness, supply and a reported stop — each one cited.

    These are the sentences a medication page carries in prose, and they are the
    answer to most of what anybody asks about a medication. They are built from
    the entity rather than re-derived, so the answer and the page agree by
    construction.
    """
    anchor = _anchor_claim(entity)
    if anchor is None:
        return []
    citation = citer.cite(anchor.cite, "Recorded claim")
    lines: list[str] = [f"status is {entity.status}"]
    if entity.stale:
        lines.append("nothing has confirmed it since it was expected to run out")
    if entity.last_confirmed is not None:
        lines.append(f"last confirmed {entity.last_confirmed.render()}")
    if entity.expected_exhaustion is not None:
        lines.append(f"expected to have run out around {entity.expected_exhaustion.render()}")
    if entity.started is not None:
        lines.append(f"started {entity.started.render()}")

    found = [
        Passage(
            key=anchor.cite,
            kind=LIFECYCLE,
            subject_id=entity.id,
            title=f"{entity.name} — current state",
            text="; ".join(lines),
            tier=entity.evidence_tier or anchor.evidence_tier,
            corrected=False,
            when="",
            citation=citation,
            rank=rank + 1,
            order=(entity.id, "~state", 0),
            facet=facet,
        )
    ]

    report = entity.stop_report
    if report is not None:
        found.append(
            Passage(
                key=report.claim.cite,
                kind=LIFECYCLE,
                subject_id=entity.id,
                title=f"{entity.name} — reported stopped",
                text=(
                    f"you reported stopping this ({report.tier}"
                    + (f", {report.when.render()}" if report.when is not None else "")
                    + "); it stays on the list because a stop below prescriber "
                    "evidence does not change the status"
                ),
                tier=report.tier,
                corrected=False,
                when=report.when.render() if report.when is not None else "",
                citation=citer.cite(report.claim.cite, "Recorded claim"),
                rank=rank + 1,
                order=(entity.id, "~stop", 0),
                facet=facet,
            )
        )
    return found


def _anchor_claim(entity: Entity) -> Claim | None:
    """Something to cite a lifecycle sentence against.

    An entity exists because a claim put it there, so there is always one. A
    sentence with no source cannot be constructed at all, which is the point.
    """
    claims = entity.supporting_claims
    if claims:
        return claims[0]
    for predicate in sorted(entity.slots):
        for claim in entity.slots[predicate].all_claims:
            return claim
    return None


def _row_passage(row: Row, citer: Citer, facet: str, rank: int, position: int) -> Passage:
    when = row.date.render()
    return Passage(
        key=row.cite,
        kind=ROW,
        subject_id=row.subject_id,
        title=when,
        text=row.text + (f" ({row.reading_text})" if row.reading_text else ""),
        tier=row.marker,
        corrected=False,
        when=when,
        citation=citer.cite(row.cite, row.cite_description),
        rank=rank,
        # Timeline rows keep the projection's own ordering, which is the order
        # the timeline screen shows them in. Grouped after entity facts so an
        # entity's own state reads before the lines that mention it.
        order=("~timeline", "", position),
        facet=facet,
    )


# -- the facets --------------------------------------------------------------


def _entities_for(question: Question, projection: Projection) -> list[tuple[str, Entity, str]]:
    """``(facet, entity, facet-name)`` for everything the question named."""
    found: list[tuple[str, Entity, str]] = []
    seen: set[str] = set()

    def add(subject_id: str, facet: str) -> None:
        entity = projection.entities.get(subject_id)
        if entity is None or subject_id in seen:
            return
        if entity.merged_into:
            target = projection.entities.get(entity.merged_into)
            if target is None or target.id in seen:
                return
            entity = target
            subject_id = target.id
        seen.add(subject_id)
        found.append((subject_id, entity, facet))

    for subject_id in question.entities:
        add(subject_id, classify_mod.ENTITY)

    for kind in question.kinds:
        for subject_id in sorted(projection.entities):
            entity = projection.entities[subject_id]
            if entity.subject.kind == kind and not entity.merged_into:
                add(subject_id, classify_mod.KIND)
    return found[:MAX_ENTITIES]


def _artefacts_of(entity: Entity) -> frozenset[str]:
    """Every artefact behind anything this entity says.

    This is what makes "what did Dr Nguyen recommend" work without a model: the
    practitioner is an entity, the documents that mention them are the artefacts
    their claims were read off, and everything else read off those same
    documents is what the question is actually asking for.
    """
    found: set[str] = set()
    for predicate in sorted(entity.slots):
        for claim in entity.slots[predicate].all_claims:
            if claim.artifact:
                found.add(claim.artifact)
    return frozenset(found)


def _artefacts_for_tiers(
    projection: Projection, tiers: Sequence[str], citer: Citer
) -> frozenset[str]:
    """Which documents a question about "the blood test" is actually about.

    Nothing in the record stores what kind of document something is — "what a
    document *is* is the model's reading of it", and the ingest event is written
    before anything has read it. So the thing that identifies a pathology report
    is that it produced lab-issued claims, and that is what this looks for.

    Recordings are the exception, and not an arbitrary one: a voice note is the
    person talking about themselves, so everything it can say is
    patient-reported by construction — the extraction schema for a transcript
    permits no other tier. A recording therefore answers to "the voice note"
    whether or not any claim has been read off it yet, which matters because the
    transcript is on its row from the moment it is typed up.
    """
    found: set[str] = set()
    reconciliation = projection.reconciliation
    if reconciliation is not None:
        for slot in reconciliation.slots.values():
            for claim in slot.supporting:
                if claim.artifact and claim.evidence_tier in tiers:
                    found.add(claim.artifact)
    if "patient-reported" in tiers:
        for short, artifact in citer.artifacts.items():
            if artifact.mime.startswith("audio/"):
                found.add(short)
    return frozenset(found)


def _in_window(row: Row, window: Window) -> bool:
    """Band overlap, not point containment.

    A row dated "around June 2026 (±15 days)" belongs in a June question, and
    testing only its point date would drop exactly the rows whose dating is
    least certain — the ones somebody going looking most needs to see.
    """
    start, end = row.date.band
    return not (end < window.start or start > window.end)


def _matches_terms(text: str, terms: Sequence[str]) -> bool:
    return any(contains_phrase(text, term) for term in terms)


# -- the whole of it ---------------------------------------------------------


def retrieve(
    question: Question,
    projection: Projection,
    citer: Citer,
    *,
    extra_terms: Sequence[str] = (),
) -> Retrieval:
    """Everything this question is allowed to be answered from.

    *extra_terms* is the one bounded expansion's contribution: terms a model
    proposed, matched here by the same code path as the question's own words.
    The model never names a file, a path or an entity id — it proposes strings,
    and this function decides what they retrieve.
    """
    if question.is_refused:
        return Retrieval(question=question)

    suppressed = rejected_wordings(projection)
    terms = tuple(dict.fromkeys(tuple(question.terms) + tuple(extra_terms)))
    passages: list[Passage] = []
    withheld = 0

    def offer(candidates: Iterable[Passage]) -> None:
        nonlocal withheld
        for passage in candidates:
            if _carries_rejected(passage, suppressed):
                withheld += 1
                continue
            passages.append(passage)

    # 1. Entities the question named, and whole kinds where it named one.
    named = _entities_for(question, projection)
    for subject_id, entity, facet in named:
        rank = 0 if facet == classify_mod.ENTITY else 4
        offer(_entity_passages(entity, citer, facet, rank))

    # 2. People: what the documents they appear in actually say.
    for subject_id, entity, _facet in named:
        if not subject_id.startswith("person:"):
            continue
        artefacts = _artefacts_of(entity)
        if not artefacts:
            continue
        for other_id in sorted(projection.entities):
            other = projection.entities[other_id]
            if other.id == subject_id or other.merged_into:
                continue
            for predicate in sorted(other.slots):
                slot = other.slots[predicate]
                for claim in slot.supporting:
                    if claim.artifact in artefacts:
                        offer([
                            _passage(
                                entity=other,
                                claim=claim,
                                citer=citer,
                                kind=FACT,
                                facet=classify_mod.PERSON,
                                rank=2,
                            )
                        ])

    # 3. Slots named by predicate alone — "what doses am I on".
    if question.predicates and not question.entities:
        for subject_id in sorted(projection.entities):
            entity = projection.entities[subject_id]
            if entity.merged_into:
                continue
            for predicate in question.predicates:
                slot = entity.slots.get(predicate)
                if slot is None:
                    continue
                offer(_predicate_passages(entity, slot, citer))

    # 4. Documents of a kind: what they said, and the rows for the documents.
    wanted_artefacts: frozenset[str] = frozenset()
    if question.tiers:
        wanted_artefacts = _artefacts_for_tiers(projection, question.tiers, citer)
        for subject_id in sorted(projection.entities):
            entity = projection.entities[subject_id]
            if entity.merged_into:
                continue
            for predicate in sorted(entity.slots):
                for claim in entity.slots[predicate].supporting:
                    if claim.evidence_tier in question.tiers:
                        offer([
                            _passage(
                                entity=entity,
                                claim=claim,
                                citer=citer,
                                kind=FACT,
                                facet=classify_mod.ARTEFACT,
                                rank=2,
                            )
                        ])

    # 5. What changed: slots carrying more than one reading.
    if question.changes:
        for subject_id in sorted(projection.entities):
            entity = projection.entities[subject_id]
            if entity.merged_into:
                continue
            for predicate in sorted(entity.slots):
                slot = entity.slots[predicate]
                if not (slot.superseded or slot.contradicted_by or slot.is_conflicted):
                    continue
                offer(_entity_passages(entity, citer, classify_mod.CHANGES, 3))
                break

    # 6. Rows: a date range, a kind of document, or a literal term.
    rows_taken = 0
    hits = 0
    for position, row in enumerate(projection.rows):
        if rows_taken >= MAX_ROWS:
            break
        facet = None
        rank = 6
        if question.window is not None and _in_window(row, question.window):
            facet, rank = classify_mod.RECENCY, 3
        elif question.tiers and (
            row.marker in question.tiers or row.cite in wanted_artefacts
        ):
            facet, rank = classify_mod.ARTEFACT, 3
        elif terms and _matches_terms(row.text, terms):
            facet, rank = classify_mod.TERMS, 4
            hits += 1
        elif question.entities and row.subject_id in question.entities:
            facet, rank = classify_mod.ENTITY, 5
        if facet is None:
            continue
        before = len(passages)
        offer([_row_passage(row, citer, facet, rank, position)])
        rows_taken += len(passages) - before

    # 7. Literal terms against the entities themselves.
    if terms:
        for subject_id in sorted(projection.entities):
            entity = projection.entities[subject_id]
            if entity.merged_into or any(p.subject_id == subject_id for p in passages):
                continue
            if not _matches_terms(entity.name, terms):
                continue
            hits += 1
            offer(_entity_passages(entity, citer, classify_mod.TERMS, 5))

    ordered = _dedupe(sorted(passages, key=lambda p: (p.rank, p.order)))
    return Retrieval(
        passages=tuple(ordered[:MAX_PASSAGES]),
        question=question,
        term_hits=hits,
        expanded_terms=tuple(extra_terms),
        withheld=withheld,
    )


def _predicate_passages(entity: Entity, slot: Slot, citer: Citer) -> list[Passage]:
    found: list[Passage] = []
    claims = slot.readings if slot.is_conflicted else slot.supporting
    for claim in claims:
        found.append(
            _passage(
                entity=entity,
                claim=claim,
                citer=citer,
                kind=READING if slot.is_conflicted else FACT,
                facet=classify_mod.PREDICATE,
                rank=1,
                suffix=" — one of two readings that disagree" if slot.is_conflicted else "",
            )
        )
    return found


def _dedupe(passages: Sequence[Passage]) -> list[Passage]:
    """One passage per distinct line, keeping the first (best-ranked) one."""
    seen: set[tuple[str, str, str]] = set()
    kept: list[Passage] = []
    for passage in passages:
        identity = (passage.key, passage.title, passage.text)
        if identity in seen:
            continue
        seen.add(identity)
        kept.append(passage)
    return kept


def vocabulary_of(projection: Projection) -> Vocabulary:
    return Vocabulary.of(projection.entities)


def today_of(as_of) -> date:
    return as_of.date()


__all__ = [
    "MAX_ENTITIES",
    "MAX_PASSAGES",
    "MAX_ROWS",
    "Passage",
    "Retrieval",
    "rejected_wordings",
    "retrieve",
    "vocabulary_of",
]
