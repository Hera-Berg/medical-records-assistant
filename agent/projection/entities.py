"""Assembling one entity's state from its reconciled facts.

The medication lifecycle lives here, and its governing rule is that **absence of
evidence is never evidence of absence**. Nothing is removed from the active list
because it stopped being mentioned. A script that should have run out becomes
``stale`` and says how long it has been since anything confirmed it; it stays on
the list, where a clinician can see it and ask.

The only thing that moves a medication to ``stopped`` is an explicit statement by
the user — a ``claim.corrected`` carrying ``status: stopped``, or a proposed stop
the user confirmed. Both are the "explicit user tap" the consequence table
requires. A model's unconfirmed reading never stops a medication, and neither
does silence.

``status`` is one field but a medication can be in several states at once, so the
field carries the most urgent — ``stopped`` before ``conflicted`` before ``stale``
before ``active`` — and ``stale``, ``last_confirmed``, ``expected_exhaustion``
and ``conflicts`` are written separately so the precedence never hides anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping

from . import dates, reconcile, subjects, tiers
from .claims import Claim
from .dates import FuzzyDate
from .dispense import Dispense
from .reconcile import Reconciliation, ReviewItem, Slot
from .subjects import Subject

ACTIVE = "active"
STALE = "stale"
STOPPED = "stopped"
CONFLICTED = "conflicted"

#: Most urgent first. The single ``status`` field takes the first that applies.
STATUS_PRECEDENCE = (STOPPED, CONFLICTED, STALE, ACTIVE)

#: Values of a ``status`` predicate that mean the thing is over.
_STOPPED_WORDS = frozenset({"stopped", "ceased", "discontinued", "stop", "resolved", "inactive"})


@dataclass(frozen=True)
class Entity:
    """One wiki page's worth of state."""

    subject: Subject
    name: str
    status: str
    slots: Mapping[str, Slot] = field(default_factory=dict)
    stale: bool = False
    last_confirmed: FuzzyDate | None = None
    expected_exhaustion: FuzzyDate | None = None
    started: FuzzyDate | None = None
    dispense: Dispense | None = None
    dispense_claim: Claim | None = None
    evidence_tier: str | None = None
    sources: tuple[str, ...] = ()
    review: tuple[ReviewItem, ...] = ()
    merged_into: str | None = None
    merged_from: tuple[str, ...] = ()
    #: The merge event that created this stub, so its page can cite the decision
    #: rather than pointing at an artefact that was never involved.
    merge_event: str | None = None

    @property
    def id(self) -> str:
        return self.subject.id

    @property
    def rel_path(self) -> str:
        return self.subject.rel_path

    @property
    def is_stub(self) -> bool:
        return self.merged_into is not None

    @property
    def conflicts(self) -> tuple[Slot, ...]:
        return tuple(
            self.slots[name] for name in sorted(self.slots) if self.slots[name].is_conflicted
        )

    @property
    def contradictions(self) -> tuple[Slot, ...]:
        return tuple(
            self.slots[name]
            for name in sorted(self.slots)
            if self.slots[name].contradicted_by
        )

    @property
    def supporting_claims(self) -> tuple[Claim, ...]:
        seen: dict[str, Claim] = {}
        for name in sorted(self.slots):
            for claim in self.slots[name].supporting:
                seen[claim.event_id] = claim
        return tuple(seen[key] for key in sorted(seen))


def elapsed_phrase(earlier: date, later: date) -> str:
    """"8 months ago", by integer arithmetic only.

    Deliberately coarse and deliberately deterministic: it is derived from
    ``as_of``, which is an input to the projection, so the same log renders the
    same words on every machine.
    """
    days = (later - earlier).days
    if days < 0:
        return "in the future"
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 45:
        return f"{days} days ago"
    months = days // 30
    if months < 24:
        return f"{months} months ago"
    return f"{days // 365} years ago"


def _slot_date(claim: Claim) -> FuzzyDate | None:
    """The best established date for a claim, never invented from ingest time."""
    if claim.occurred_at is not None:
        return claim.occurred_at
    if claim.artifact_ts:
        parsed = dates.parse_iso_date(claim.artifact_ts[:10])
        if parsed is not None:
            return FuzzyDate(parsed)
    return None


def _latest_evidence(claims: tuple[Claim, ...]) -> FuzzyDate | None:
    best: FuzzyDate | None = None
    for claim in claims:
        candidate = _slot_date(claim)
        if candidate is None:
            continue
        if best is None or dates.sort_key(candidate) > dates.sort_key(best):
            best = candidate
    return best


def _is_stop(slot: Slot) -> bool:
    """Whether this ``status`` slot records an explicit stop by the user.

    A model's reading is not enough. Either the user corrected the value, or the
    user confirmed a proposal that said so — both are explicit acts recorded
    under a user actor in the log, which is what the consequence table means by
    a tap.
    """
    winner = slot.winner
    if winner is None:
        return False
    stated = winner.value.fields.get("status") or winner.value.literal.strip().lower()
    if stated not in _STOPPED_WORDS:
        return False
    return winner.is_correction or slot.review_state == reconcile.REVIEW_CONFIRMED


def _pick_dispense(claims: tuple[Claim, ...]) -> tuple[Dispense | None, Claim | None]:
    """The most recent supporting claim that carries a countable supply."""
    best: tuple[Dispense, Claim] | None = None
    for claim in claims:
        supply = claim.dispense
        if supply is None or not supply.is_computable:
            continue
        if best is None or claim.sort_key > best[1].sort_key:
            best = (supply, claim)
    if best is not None:
        return best
    for claim in sorted(claims, key=lambda c: c.sort_key, reverse=True):
        if claim.dispense is not None:
            return claim.dispense, claim
    return None, None


def build(
    subject: Subject,
    slots: Mapping[str, Slot],
    as_of: datetime,
    review: tuple[ReviewItem, ...] = (),
    merged_from: tuple[str, ...] = (),
) -> Entity:
    """Assemble one entity from its reconciled slots."""
    supporting: dict[str, Claim] = {}
    for name in sorted(slots):
        for claim in slots[name].supporting:
            supporting[claim.event_id] = claim
    claims = tuple(supporting[key] for key in sorted(supporting))

    name_slot = slots.get("name")
    name = (
        name_slot.winner.value.literal
        if name_slot is not None and name_slot.winner is not None
        else subjects.display_name(subject)
    )

    last_confirmed = _latest_evidence(claims)
    # Only ever what a claim actually states. The earliest evidence we happen to
    # hold is when the record starts, not when the medication started, and
    # writing one into the other is the same substitution the four timestamps
    # exist to prevent — invisible, and wrong by years.
    started_slot = slots.get("started")
    started = (
        started_slot.winner.occurred_at
        if started_slot is not None and started_slot.winner is not None
        else None
    )

    supply, supply_claim = _pick_dispense(claims)
    exhaustion = None
    if supply is not None and supply_claim is not None:
        exhaustion = supply.exhaustion(_slot_date(supply_claim))

    stale = False
    if exhaustion is not None:
        run_out = exhaustion.band[1]
        confirmed_since = (
            last_confirmed is not None and last_confirmed.band[0] > run_out
        )
        stale = as_of.date() > run_out and not confirmed_since

    status_slot = slots.get("status")
    stopped = status_slot is not None and _is_stop(status_slot)
    conflicted = any(slot.is_conflicted for slot in slots.values())

    if stopped:
        status = STOPPED
    elif conflicted:
        status = CONFLICTED
    elif stale:
        status = STALE
    else:
        status = ACTIVE

    tier = None
    ranked = [c.evidence_tier for c in claims]
    if ranked:
        tier = max(ranked, key=tiers.evidence_rank)

    sources = tuple(sorted({claim.cite for claim in claims}))

    return Entity(
        subject=subject,
        name=name,
        status=status,
        slots=dict(slots),
        stale=stale,
        last_confirmed=last_confirmed,
        expected_exhaustion=exhaustion,
        started=started,
        dispense=supply,
        dispense_claim=supply_claim,
        evidence_tier=tier,
        sources=sources,
        review=review,
        merged_from=merged_from,
    )


def build_all(reconciliation: Reconciliation, as_of: datetime) -> dict[str, Entity]:
    """Every entity the admitted claims support, plus stubs for merged names."""
    entities: dict[str, Entity] = {}
    merged_from: dict[str, list[str]] = {}
    for source, into in reconciliation.aliases.items():
        merged_from.setdefault(into, []).append(source)

    review_by_subject: dict[str, list[ReviewItem]] = {}
    for item in reconciliation.review:
        review_by_subject.setdefault(item.subject_id, []).append(item)

    for subject_id in reconciliation.subject_ids:
        subject = subjects.parse(subject_id)
        if subject is None:  # pragma: no cover - slots are keyed by parsed subjects
            continue
        entities[subject_id] = build(
            subject,
            reconciliation.slots_for(subject_id),
            as_of,
            review=tuple(review_by_subject.get(subject_id, ())),
            merged_from=tuple(sorted(merged_from.get(subject_id, ()))),
        )

    # A merged-away name keeps a stub, so following an old citation or an old
    # filename still lands somewhere that explains what happened.
    for source, into in sorted(reconciliation.aliases.items()):
        if source in entities or into not in entities:
            continue
        subject = subjects.parse(source)
        if subject is None:
            continue
        entities[source] = Entity(
            subject=subject,
            name=subjects.display_name(subject),
            status=entities[into].status,
            merged_into=into,
            merge_event=reconciliation.alias_events.get(source),
        )
    return entities
