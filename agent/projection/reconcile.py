"""Reducing a stream of claims into what the record currently says.

This is the hard part of the project, and the order of the two rules that follow
is the whole of it.

**Admission first, ranking second.** A claim is only considered at all once the
consequence gate has let it in, and that gate reads nothing but the predicate,
the event types around it, and the clock. No confidence value and no amount of
elapsed time admits a high-consequence claim; only a user event does. Putting
this before ranking means a high-tier claim cannot win its way into the wiki by
being the most authoritative reading available.

**Corrections outrank extractions regardless of order.** Replaying by timestamp
and letting the last write win reintroduces exactly the errors a user has already
fixed — the spec calls this the single most likely bug in the system. A
correction therefore wins over any extraction whatever their timestamps, and a
re-extraction that disagrees with it raises a contradiction for a human instead
of quietly overwriting it.

Everything here is a pure function of ``(events, as_of)``. Nothing reads the
clock, so two rebuilds of the same log at the same ``as_of`` agree byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from ..events.envelope import Event, parse_ts_or_none
from . import claims as claims_mod
from . import dates, tiers
from .claims import Claim, ClaimProblem

#: How long an untouched medium-consequence claim waits before it applies
#: itself. The spec's weekly review is what this number is for: a claim that has
#: sat through a whole review cycle untouched has been passed over, not missed.
MEDIUM_REVIEW_DAYS = 7

# Admission outcomes.
ADMITTED = "admitted"
PENDING = "pending"
REJECTED = "rejected"
SUPERSEDED = "superseded"

# How a slot resolved.
SETTLED = "settled"
CONFLICTED = "conflicted"
CONTRADICTED = "contradicted"

# Why a slot's value is where it is, for the reader.
REVIEW_CONFIRMED = "confirmed"
REVIEW_UNREVIEWED = "unreviewed"
REVIEW_AUTO = "auto"


@dataclass(frozen=True)
class Admission:
    """Whether one proposed claim is allowed to count, and why."""

    claim: Claim
    state: str
    review_state: str
    reason: str
    decided_by: str | None = None


@dataclass(frozen=True)
class Slot:
    """One ``(subject, predicate)`` fact, after reconciliation."""

    subject_id: str
    predicate: str
    consequence: str
    resolution: str
    winner: Claim | None = None
    review_state: str = REVIEW_AUTO
    #: For a conflicted slot: one claim per distinct reading, both rendered.
    readings: tuple[Claim, ...] = ()
    #: For a contradicted slot: admitted extractions that disagree with the
    #: user's correction. The correction still wins.
    contradicted_by: tuple[Claim, ...] = ()
    #: Earlier readings this one replaced, most recent first.
    superseded: tuple[Claim, ...] = ()
    #: Claims the consequence gate is holding back.
    pending: tuple[Claim, ...] = ()

    @property
    def is_conflicted(self) -> bool:
        return self.resolution == CONFLICTED

    @property
    def has_value(self) -> bool:
        return self.winner is not None

    @property
    def supporting(self) -> tuple[Claim, ...]:
        """Every claim that currently stands behind this slot."""
        if self.winner is not None:
            return (self.winner,)
        return self.readings


@dataclass(frozen=True)
class ReviewItem:
    """Something waiting on a person. Phase 7 renders these; phase 3 counts them."""

    kind: str  # awaiting-confirmation | conflict | contradiction
    consequence: str
    subject_id: str
    predicate: str
    summary: str
    claims: tuple[Claim, ...] = ()

    @property
    def sort_key(self) -> tuple[Any, ...]:
        return (
            -tiers.consequence_rank(self.consequence),
            self.subject_id,
            self.predicate,
            self.kind,
            tuple(c.event_id for c in self.claims),
        )


@dataclass(frozen=True)
class Reconciliation:
    """Everything the projection needs, and everything that went wrong."""

    slots: Mapping[tuple[str, str], Slot] = field(default_factory=dict)
    review: tuple[ReviewItem, ...] = ()
    problems: tuple[ClaimProblem, ...] = ()
    anomalies: tuple[str, ...] = ()
    aliases: Mapping[str, str] = field(default_factory=dict)
    #: Subject id -> the id of the merge event that aliased it, so a stub page
    #: can cite the decision that created it.
    alias_events: Mapping[str, str] = field(default_factory=dict)
    admissions: tuple[Admission, ...] = ()

    def slots_for(self, subject_id: str) -> dict[str, Slot]:
        return {
            predicate: slot
            for (subject, predicate), slot in self.slots.items()
            if subject == subject_id
        }

    @property
    def subject_ids(self) -> tuple[str, ...]:
        return tuple(sorted({subject for subject, _ in self.slots}))


def _latest_decision(events: Iterable[Event]) -> dict[str, Event]:
    """The last user decision recorded against each proposed claim.

    Ordering is by ``(ts, id)`` like everything else, so two devices deciding the
    same item resolve identically wherever the log is read.
    """
    latest: dict[str, Event] = {}
    for event in events:
        if event.type not in ("claim.confirmed", "claim.rejected", "claim.corrected"):
            continue
        target = event.payload.get("target")
        if not isinstance(target, str) or not target:
            continue
        current = latest.get(target)
        if current is None or event.sort_key > current.sort_key:
            latest[target] = event
    return latest


def _alias_map(events: Iterable[Event]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Confirmed entity merges, and the reverts that undo them.

    Merges are events, never silent normalisation: the latest decision for a
    pair wins and a revert genuinely puts the entity back.
    """
    decisions: dict[tuple[str, str], tuple[tuple[Any, ...], bool, str]] = {}
    anomalies: list[str] = []
    for event in events:
        if event.type not in ("entity.merge.confirmed", "entity.merge.reverted"):
            continue
        source = event.payload.get("from")
        into = event.payload.get("into")
        if not isinstance(source, str) or not isinstance(into, str):
            anomalies.append(
                f"{event.id}: merge event needs 'from' and 'into' subject ids; ignored"
            )
            continue
        pair = (source, into)
        confirmed = event.type == "entity.merge.confirmed"
        current = decisions.get(pair)
        if current is None or event.sort_key > current[0]:
            decisions[pair] = (event.sort_key, confirmed, event.id)

    direct = {
        source: into
        for (source, into), (_, confirmed, _event_id) in sorted(decisions.items())
        if confirmed
    }
    decided_by = {
        source: event_id
        for (source, _into), (_, confirmed, event_id) in sorted(decisions.items())
        if confirmed
    }

    resolved: dict[str, str] = {}
    for source in sorted(direct):
        seen = [source]
        target = direct[source]
        while target in direct and target not in seen:
            seen.append(target)
            target = direct[target]
        if target in seen:
            anomalies.append(
                f"merge chain starting at {source} loops back on itself; left unmerged"
            )
            continue
        resolved[source] = target
    return resolved, decided_by, anomalies


def _admit(claim: Claim, decision: Event | None, as_of: datetime) -> Admission:
    """The consequence gate. The only place a claim becomes eligible to count."""
    if decision is not None:
        if decision.type == "claim.rejected":
            return Admission(claim, REJECTED, REVIEW_CONFIRMED, "rejected by you", decision.id)
        if decision.type == "claim.corrected":
            return Admission(
                claim, SUPERSEDED, REVIEW_CONFIRMED, "replaced by your correction", decision.id
            )
        return Admission(claim, ADMITTED, REVIEW_CONFIRMED, "confirmed by you", decision.id)

    if claim.consequence == tiers.HIGH:
        # No exception, no confidence threshold, no elapsed time. A confident
        # wrong allergy is the failure that matters.
        return Admission(
            claim,
            PENDING,
            REVIEW_UNREVIEWED,
            "waiting for you to confirm it; high-consequence claims never apply on their own",
        )

    if claim.consequence == tiers.MEDIUM:
        recorded = parse_ts_or_none(claim.ts)
        if recorded is None:
            return Admission(
                claim, PENDING, REVIEW_UNREVIEWED, "the claim's timestamp is unreadable"
            )
        if as_of - recorded >= timedelta(days=MEDIUM_REVIEW_DAYS):
            return Admission(
                claim,
                ADMITTED,
                REVIEW_UNREVIEWED,
                f"applied automatically after {MEDIUM_REVIEW_DAYS} days without review",
            )
        return Admission(
            claim,
            PENDING,
            REVIEW_UNREVIEWED,
            f"waiting for the weekly review; it applies on its own after "
            f"{MEDIUM_REVIEW_DAYS} days",
        )

    return Admission(claim, ADMITTED, REVIEW_AUTO, "low-consequence, applied automatically")


def _maximal_by_date(candidates: list[Claim]) -> list[Claim]:
    """Drop candidates another candidate is unambiguously later than.

    Ordering is deliberately partial. Two scripts a month apart are a dose
    change and the later one wins; two scripts whose date bands overlap — or
    either of which has no date — cannot be ordered at all, and both survive
    here to be reported as a conflict rather than silently resolved.
    """
    survivors: list[Claim] = []
    for candidate in candidates:
        beaten = any(
            other is not candidate
            and dates.can_order(other.occurred_at, candidate.occurred_at)
            and dates.is_later(other.occurred_at, candidate.occurred_at)
            for other in candidates
        )
        if not beaten:
            survivors.append(candidate)
    return survivors


def _distinct_readings(candidates: list[Claim]) -> list[Claim]:
    """One representative claim per distinct value, in a stable order."""
    by_value: dict[str, Claim] = {}
    for candidate in candidates:
        current = by_value.get(candidate.value.key)
        if current is None or candidate.sort_key > current.sort_key:
            by_value[candidate.value.key] = candidate
    return [by_value[key] for key in sorted(by_value)]


def _resolve(
    subject_id: str,
    predicate: str,
    admitted: list[Claim],
    pending: list[Claim],
    review_states: Mapping[str, str],
) -> tuple[Slot, list[ReviewItem]]:
    """Rank the admitted claims for one slot and say how it resolved."""
    consequence = (
        admitted[0].consequence
        if admitted
        else (pending[0].consequence if pending else tiers.HIGH)
    )
    review: list[ReviewItem] = []

    corrections = [c for c in admitted if c.is_correction]
    extractions = [c for c in admitted if not c.is_correction]

    if corrections:
        winner = max(corrections, key=lambda c: c.sort_key)
        disagreeing = tuple(
            sorted(
                (e for e in extractions if e.value.key != winner.value.key),
                key=lambda c: c.sort_key,
                reverse=True,
            )
        )
        others = [c for c in admitted if c is not winner and c not in disagreeing]
        resolution = CONTRADICTED if disagreeing else SETTLED
        if disagreeing:
            review.append(
                ReviewItem(
                    kind="contradiction",
                    consequence=consequence,
                    subject_id=subject_id,
                    predicate=predicate,
                    summary=(
                        f"{subject_id} {predicate}: a later reading disagrees with your "
                        f"correction, which still stands"
                    ),
                    claims=(winner,) + disagreeing,
                )
            )
        return (
            Slot(
                subject_id=subject_id,
                predicate=predicate,
                consequence=consequence,
                resolution=resolution,
                winner=winner,
                review_state=REVIEW_CONFIRMED,
                contradicted_by=disagreeing,
                superseded=tuple(sorted(others, key=lambda c: c.sort_key, reverse=True)),
                pending=tuple(sorted(pending, key=lambda c: c.sort_key, reverse=True)),
            ),
            review,
        )

    if not extractions:
        return (
            Slot(
                subject_id=subject_id,
                predicate=predicate,
                consequence=consequence,
                resolution=SETTLED,
                winner=None,
                pending=tuple(sorted(pending, key=lambda c: c.sort_key, reverse=True)),
            ),
            review,
        )

    top_rank = max(tiers.evidence_rank(c.evidence_tier) for c in extractions)
    contenders = [c for c in extractions if tiers.evidence_rank(c.evidence_tier) == top_rank]
    contenders = _maximal_by_date(contenders)
    readings = _distinct_readings(contenders)

    if len(readings) > 1:
        # Two sources of equal authority that cannot be told apart by date.
        # Show both, name both sources, pick neither, average nothing.
        superseded = [c for c in extractions if c not in readings]
        review.append(
            ReviewItem(
                kind="conflict",
                consequence=consequence,
                subject_id=subject_id,
                predicate=predicate,
                summary=(
                    f"{subject_id} {predicate}: {len(readings)} sources of equal "
                    f"standing disagree and none has been chosen"
                ),
                claims=tuple(readings),
            )
        )
        return (
            Slot(
                subject_id=subject_id,
                predicate=predicate,
                consequence=consequence,
                resolution=CONFLICTED,
                winner=None,
                review_state=min(
                    (review_states.get(c.event_id, REVIEW_AUTO) for c in readings),
                    key=_review_order,
                ),
                readings=tuple(readings),
                superseded=tuple(sorted(superseded, key=lambda c: c.sort_key, reverse=True)),
                pending=tuple(sorted(pending, key=lambda c: c.sort_key, reverse=True)),
            ),
            review,
        )

    winner = max(contenders, key=lambda c: c.sort_key)
    superseded = [c for c in extractions if c is not winner]
    return (
        Slot(
            subject_id=subject_id,
            predicate=predicate,
            consequence=consequence,
            resolution=SETTLED,
            winner=winner,
            review_state=review_states.get(winner.event_id, REVIEW_AUTO),
            superseded=tuple(sorted(superseded, key=lambda c: c.sort_key, reverse=True)),
            pending=tuple(sorted(pending, key=lambda c: c.sort_key, reverse=True)),
        ),
        review,
    )


_REVIEW_ORDER = {REVIEW_UNREVIEWED: 0, REVIEW_AUTO: 1, REVIEW_CONFIRMED: 2}


def _review_order(state: str) -> int:
    """Least-reviewed first, so a slot never looks better reviewed than its parts."""
    return _REVIEW_ORDER.get(state, 0)


def reconcile(events: Iterable[Event], as_of: datetime) -> Reconciliation:
    """Reduce the event stream into fact slots, a review queue, and problems."""
    events = list(events)
    decisions = _latest_decision(events)
    aliases, alias_events, anomalies = _alias_map(events)

    problems: list[ClaimProblem] = []
    proposals: dict[str, Claim] = {}
    admissions: list[Admission] = []
    admitted_by_slot: dict[tuple[str, str], list[Claim]] = {}
    pending_by_slot: dict[tuple[str, str], list[Claim]] = {}
    review_states: dict[str, str] = {}
    review: list[ReviewItem] = []

    def slot_key(claim: Claim) -> tuple[str, str]:
        return (aliases.get(claim.subject.id, claim.subject.id), claim.predicate)

    # Proposals first: a correction may need the claim it is amending.
    for event in events:
        if event.type != "claim.proposed":
            continue
        parsed = claims_mod.parse(event)
        if isinstance(parsed, ClaimProblem):
            problems.append(parsed)
            continue
        proposals[event.id] = parsed
        anomaly = claims_mod.declared_tier_anomaly(parsed)
        if anomaly:
            anomalies.append(anomaly)

        admission = _admit(parsed, decisions.get(event.id), as_of)
        admissions.append(admission)
        review_states[parsed.event_id] = admission.review_state
        if admission.state == ADMITTED:
            admitted_by_slot.setdefault(slot_key(parsed), []).append(parsed)
        elif admission.state == PENDING:
            pending_by_slot.setdefault(slot_key(parsed), []).append(parsed)
            review.append(
                ReviewItem(
                    kind="awaiting-confirmation",
                    consequence=parsed.consequence,
                    subject_id=slot_key(parsed)[0],
                    predicate=parsed.predicate,
                    summary=(
                        f"{slot_key(parsed)[0]} {parsed.predicate}: {admission.reason}"
                    ),
                    claims=(parsed,),
                )
            )

    for event in events:
        if event.type != "claim.corrected":
            continue
        target_id = event.payload.get("target")
        target = proposals.get(target_id) if isinstance(target_id, str) else None
        if isinstance(target_id, str) and target_id and target is None:
            anomalies.append(
                f"{event.id}: correction targets {target_id}, which is not a proposed "
                f"claim in this log; read on its own terms"
            )
        parsed = claims_mod.parse(event, target=target)
        if isinstance(parsed, ClaimProblem):
            problems.append(parsed)
            continue
        review_states[parsed.event_id] = REVIEW_CONFIRMED
        admissions.append(
            Admission(parsed, ADMITTED, REVIEW_CONFIRMED, "your correction, which always stands")
        )
        admitted_by_slot.setdefault(slot_key(parsed), []).append(parsed)

    slots: dict[tuple[str, str], Slot] = {}
    for key in sorted(set(admitted_by_slot) | set(pending_by_slot)):
        subject_id, predicate = key
        slot, items = _resolve(
            subject_id,
            predicate,
            admitted_by_slot.get(key, []),
            pending_by_slot.get(key, []),
            review_states,
        )
        # A slot with nothing admitted has no place in the wiki at all; its
        # pending claims are already in the review queue.
        if slot.has_value or slot.readings:
            slots[key] = slot
        review.extend(items)

    return Reconciliation(
        slots=slots,
        review=tuple(sorted(review, key=lambda item: item.sort_key)),
        problems=tuple(problems),
        anomalies=tuple(anomalies),
        aliases=aliases,
        alias_events=alias_events,
        admissions=tuple(admissions),
    )
