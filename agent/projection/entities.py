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

The confirmed-proposal path carries an extra constraint the correction path does
not: the proposal must be ``prescriber-issued`` or ``lab-issued``. Confirming is
one tap and typing a correction is not, so the cheaper act is the one that has to
be backed by a document that can actually cease a medication.

**A stop below that tier annotates rather than transitions, and is never
discarded.** Declining to act on a user's tap is legitimate; making it vanish is
not, and is worse than the outcome the gate was protecting against — the user
believes they told the record something. So the status stays ``active`` or
``stale`` and the entity carries a :class:`StopReport`, which becomes
``stop_reported`` and ``stop_reported_tier`` in frontmatter, a cited sentence in
the body, and an entry in the review queue.

That is not a grudging compromise. A patient saying they stopped taking something
is real information: they are the authority on what they actually take, while the
prescriber is the authority on what was prescribed, and a record showing both with
the discrepancy visible is more useful to a clinician than either alone. It is a
discrepancy and not ``conflicted`` — that status is reserved for contradictory
sources for the same fact.

``status`` is one field but a medication can be in several states at once, so the
field carries the most urgent — ``stopped`` before ``conflicted`` before ``stale``
before ``active`` — and ``stale``, ``last_confirmed``, ``expected_exhaustion``
and ``conflicts`` are written separately so the precedence never hides anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping

from . import anomalies as anomalies_mod
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

#: Review queue kind for a stop the tier rule declined to act on. Its own kind
#: rather than a ``conflict``: nothing here contradicts anything, the patient and
#: the prescriber are simply answering different questions.
STOP_REPORTED = "reported-stop"

#: Values of a ``status`` predicate that mean the thing is over.
_STOPPED_WORDS = frozenset({"stopped", "ceased", "discontinued", "stop", "resolved", "inactive"})

#: Evidence tiers a *proposed* stop may carry. A confirmed proposal is one tap,
#: and a tap is cheaper than a correction, so the document behind the proposal
#: has to be one that can actually cease a medication. ``patient-reported`` and
#: ``inferred`` can never produce a stop however emphatically they are confirmed
#: — the user saying "I think I stopped that" is a correction to type, not a
#: proposal to accept. A ``claim.corrected`` is exempt: the user authored the
#: value itself, so there is no extractor's reading to vouch for.
#:
#: This is one half of the guard. The other half is phase 7's: the review inbox
#: must render a stop proposal as its own distinct action, never as a generic
#: accept in a tap-through queue. See rule 4 in ``CLAUDE.md``.
_STOP_TIERS = frozenset({"prescriber-issued", "lab-issued"})


@dataclass(frozen=True)
class StopReport:
    """A stop the user endorsed that the tier rule declined to act on.

    Kept beside the status rather than folded into it: the medication is still
    on the list, and this says who reported it stopped and when.
    """

    claim: Claim
    tier: str
    when: FuzzyDate | None

    @property
    def iso(self) -> str | None:
        return self.when.iso if self.when is not None else None


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
    #: A stop the user endorsed that could not transition the status. Never
    #: ``None`` merely because the rule declined it — see the module docstring.
    stop_report: StopReport | None = None
    #: Anomalies that resolve to this subject. Subject-less ones stay in the
    #: rebuild report; these appear here as well, because this page is where a
    #: reader asking "is this claim gated correctly" would look.
    anomalies: tuple[anomalies_mod.Anomaly, ...] = ()
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


def _stated_stop(claim: Claim) -> bool:
    """Whether this ``status`` claim says the thing is over."""
    stated = claim.value.fields.get("status") or claim.value.literal.strip().lower()
    return stated in _STOPPED_WORDS


def _stop_report(slot: Slot | None) -> StopReport | None:
    """A user-endorsed stop the tier rule will not act on.

    Searched across everything the gate admitted, not just the winner: a
    higher-tier ``status`` reading can outrank the patient's, and the whole point
    of this function is that being outranked must not be the same as being
    forgotten.
    """
    if slot is None:
        return None
    candidates = [
        c
        for c in slot.endorsed_claims()
        if not c.is_correction and _stated_stop(c) and c.evidence_tier not in _STOP_TIERS
    ]
    if not candidates:
        return None
    # The most recent one the user endorsed. Earlier ones stay in the slot's
    # history, which the page's "Earlier readings" section prints.
    claim = max(candidates, key=lambda c: c.sort_key)
    return StopReport(claim=claim, tier=claim.evidence_tier, when=_slot_date(claim))


def _is_stop(slot: Slot) -> bool:
    """Whether this ``status`` slot records an explicit stop by the user.

    A model's reading is not enough. Either the user corrected the value, or the
    user confirmed a proposal that said so — both are explicit acts recorded
    under a user actor in the log, which is what the consequence table means by
    a tap.

    The two paths are not equally cheap, so they are not equally trusted. A
    correction is the user authoring the value and stands on its own. A confirmed
    proposal is one tap on an extractor's reading, so the reading has to come
    from a source that can cease a medication: see :data:`_STOP_TIERS`.
    """
    winner = slot.winner
    if winner is None:
        return False
    if not _stated_stop(winner):
        return False
    if winner.is_correction:
        return True
    if slot.review_state != reconcile.REVIEW_CONFIRMED:
        return False
    return winner.evidence_tier in _STOP_TIERS


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
    notes: tuple[anomalies_mod.Anomaly, ...] = (),
) -> Entity:
    """Assemble one entity from its reconciled slots."""
    supporting: dict[str, Claim] = {}
    cited: dict[str, Claim] = {}
    for name in sorted(slots):
        for claim in slots[name].supporting:
            supporting[claim.event_id] = claim
        for claim in slots[name].all_claims:
            cited[claim.event_id] = claim
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
    if stopped:
        # Both of these are projections forward from a script, and neither
        # survives the medication being stopped. A page carrying
        # `expected_exhaustion: 2026-10-07` under `status: stopped` reads to a
        # clinician skimming it as a live supply, and `stale` would additionally
        # be saying that nothing has confirmed a medication nobody is taking.
        #
        # Only the derived projections go. The dispense spans stay on the
        # entity and the Supply section renders them in the past tense, because
        # rule 4 keeps a stopped medication's file and its full history.
        exhaustion = None
        stale = False
    # Only worth reporting while the medication is still on the list. Once a
    # prescriber-issued stop has transitioned it there is no discrepancy left to
    # show, and the earlier report stays visible in the slot's history.
    stop_report = None if stopped else _stop_report(status_slot)
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

    # Every artefact the page cites, not only the ones currently holding a
    # value: an earlier reading is footnoted in the history, and frontmatter that
    # omits its artefact sends a reader looking for a source the page does not
    # list. Derived fields above deliberately stay on the narrower set — a
    # superseded reading is not evidence that a medication is still current.
    sources = tuple(sorted({cited[key].cite for key in sorted(cited)}))

    if stop_report is not None:
        review = review + (
            ReviewItem(
                kind=STOP_REPORTED,
                consequence=status_slot.consequence,
                subject_id=subject.id,
                predicate="status",
                summary=(
                    f"{subject.id} status: you confirmed a {stop_report.tier} stop. It is "
                    f"recorded on the page, but only a prescriber-issued or lab-issued "
                    f"source can take a medication off the list"
                ),
                claims=(stop_report.claim,),
            ),
        )

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
        stop_report=stop_report,
        anomalies=notes,
        merged_from=merged_from,
    )


def derived_review(entities: Mapping[str, Entity]) -> tuple[ReviewItem, ...]:
    """Review items that only exist once entities are assembled.

    :func:`reconcile.reconcile` works slot by slot and cannot see a medication's
    lifecycle, so the reported-stop item is raised here and merged back into the
    projection's queue.
    """
    items: list[ReviewItem] = []
    for subject_id in sorted(entities):
        items.extend(
            item for item in entities[subject_id].review if item.kind == STOP_REPORTED
        )
    return tuple(items)


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
            notes=anomalies_mod.for_subject(reconciliation.anomalies, subject_id),
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
