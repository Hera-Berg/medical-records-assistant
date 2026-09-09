"""Reducing a stream of claims into what the record currently says.

This is the hard part of the project, and the order of the two rules that follow
is the whole of it.

**Admission first, ranking second.** A claim is only considered at all once the
consequence gate has let it in, and that gate reads nothing but the predicate,
the event types around it, and the clock. No confidence value and no amount of
elapsed time admits a high-consequence claim; only a user event does. Putting
this before ranking means a high-tier claim cannot win its way into the wiki by
being the most authoritative reading available.

**Nothing a value replaced is discarded.** A reading that lost on rank and a
reading the user corrected both stay on the slot, and the entity page renders
them with their citations and what replaced them. The point is not that the
extraction deserves a voice — it is that the user's own act is unauditable
without the thing it acted on, and a mistyped correction is undetectable once the
original is gone.

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
from .anomalies import Anomaly
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
    #: Earlier readings this value replaced, most recent first. Includes both
    #: readings that lost on rank and readings the gate set aside because the
    #: user corrected them — "what did I correct, and from what" has to be
    #: answerable from the folder alone, and a correction whose original has
    #: vanished is unverifiable.
    superseded: tuple[Claim, ...] = ()
    #: Claims the consequence gate is holding back.
    pending: tuple[Claim, ...] = ()
    #: Event ids of admitted claims the user personally endorsed — confirmed, or
    #: authored as a correction. Ranking can move any of these out of ``winner``,
    #: so a rule that needs to know "did the user actually say this" has to ask
    #: the slot rather than reading the winner and calling it the whole story.
    endorsed: frozenset[str] = frozenset()

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

    @property
    def all_claims(self) -> tuple[Claim, ...]:
        """Every claim this slot carries, whatever became of it.

        ``superseded`` and ``contradicted_by`` hold claims that lost but were
        never discarded, and a user-endorsed claim can be sitting in either.
        Not all of these were admitted — ``superseded`` also holds readings the
        gate set aside because the user corrected them.
        """
        seen: dict[str, Claim] = {}
        for claim in (
            ((self.winner,) if self.winner is not None else ())
            + self.readings
            + self.superseded
            + self.contradicted_by
        ):
            seen.setdefault(claim.event_id, claim)
        return tuple(seen[key] for key in sorted(seen))

    def endorsed_claims(self) -> tuple[Claim, ...]:
        """The admitted claims the user personally confirmed or authored."""
        return tuple(c for c in self.all_claims if c.event_id in self.endorsed)


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


def _latest_decision(events: Iterable[Event]) -> tuple[dict[str, Event], list[Anomaly]]:
    """The last user decision recorded against each proposed claim.

    Ordering is by ``(ts, id)`` like everything else, so two devices deciding the
    same item resolve identically wherever the log is read.

    A decision naming no target cannot be acted on, and rule 3 does not allow it
    to simply disappear: it comes back as an anomaly. The user tapped something
    and is entitled to find out that the record could not tell what.
    """
    latest: dict[str, Event] = {}
    anomalies: list[Anomaly] = []
    for event in events:
        if event.type not in ("claim.confirmed", "claim.rejected", "claim.corrected"):
            continue
        target = event.payload.get("target")
        if not isinstance(target, str) or not target:
            # A correction can stand on its own terms if it names a subject and
            # predicate itself; the other two decide *about* a claim and have
            # nothing left to mean without one.
            if event.type != "claim.corrected":
                anomalies.append(
                    Anomaly(
                        f"{event.id}: {event.type} names no target claim, so your "
                        f"decision could not be applied to anything"
                    )
                )
            continue
        current = latest.get(target)
        if current is None or event.sort_key > current.sort_key:
            latest[target] = event
    return latest, anomalies


def _alias_map(
    events: Iterable[Event],
) -> tuple[dict[str, str], dict[str, str], list[Anomaly]]:
    """Confirmed entity merges, and the reverts that undo them.

    Merges are events, never silent normalisation: the latest decision for a
    pair wins and a revert genuinely puts the entity back.
    """
    decisions: dict[tuple[str, str], tuple[tuple[Any, ...], bool, str]] = {}
    anomalies: list[Anomaly] = []
    for event in events:
        if event.type not in ("entity.merge.confirmed", "entity.merge.reverted"):
            continue
        source = event.payload.get("from")
        into = event.payload.get("into")
        if not isinstance(source, str) or not isinstance(into, str):
            anomalies.append(
                Anomaly(f"{event.id}: merge event needs 'from' and 'into' subject ids; ignored")
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
                Anomaly(
                    f"merge chain starting at {source} loops back on itself; left unmerged",
                    subject_id=source,
                    cite=f"ev-{decided_by[source]}",
                )
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
    replaced: list[Claim] | None = None,
) -> tuple[Slot, list[ReviewItem]]:
    """Rank the admitted claims for one slot and say how it resolved.

    *replaced* holds readings the gate set aside before ranking — a proposal the
    user corrected never becomes a contender, but it is the thing their
    correction acted on. It joins ``superseded`` so the page can show it.
    """
    consequence = (
        admitted[0].consequence
        if admitted
        else (pending[0].consequence if pending else tiers.HIGH)
    )
    review: list[ReviewItem] = []
    # Recorded before ranking, because ranking is exactly what can bury one of
    # these behind a higher-tier reading.
    endorsed = frozenset(
        c.event_id for c in admitted if review_states.get(c.event_id) == REVIEW_CONFIRMED
    )
    replaced = list(replaced or ())

    def earlier(*groups: Iterable[Claim]) -> tuple[Claim, ...]:
        """Everything a value replaced, most recent first, each named once."""
        seen: dict[str, Claim] = {}
        for group in groups:
            for claim in group:
                seen.setdefault(claim.event_id, claim)
        return tuple(sorted(seen.values(), key=lambda c: c.sort_key, reverse=True))

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
                superseded=earlier(others, replaced),
                pending=tuple(sorted(pending, key=lambda c: c.sort_key, reverse=True)),
                endorsed=endorsed,
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
                superseded=earlier(replaced),
                pending=tuple(sorted(pending, key=lambda c: c.sort_key, reverse=True)),
                endorsed=endorsed,
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
                superseded=earlier(superseded, replaced),
                pending=tuple(sorted(pending, key=lambda c: c.sort_key, reverse=True)),
                endorsed=endorsed,
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
            superseded=earlier(superseded, replaced),
            pending=tuple(sorted(pending, key=lambda c: c.sort_key, reverse=True)),
            endorsed=endorsed,
        ),
        review,
    )


#: How a decision event reads in prose, for the anomaly lines above.
_DECIDED = {"claim.confirmed": "confirmed", "claim.rejected": "rejected"}


_REVIEW_ORDER = {REVIEW_UNREVIEWED: 0, REVIEW_AUTO: 1, REVIEW_CONFIRMED: 2}


def _review_order(state: str) -> int:
    """Least-reviewed first, so a slot never looks better reviewed than its parts."""
    return _REVIEW_ORDER.get(state, 0)


def reconcile(events: Iterable[Event], as_of: datetime) -> Reconciliation:
    """Reduce the event stream into fact slots, a review queue, and problems."""
    events = list(events)
    decisions, decision_anomalies = _latest_decision(events)
    aliases, alias_events, anomalies = _alias_map(events)
    anomalies = list(anomalies) + decision_anomalies

    problems: list[ClaimProblem] = []
    proposals: dict[str, Claim] = {}
    admissions: list[Admission] = []
    admitted_by_slot: dict[tuple[str, str], list[Claim]] = {}
    pending_by_slot: dict[tuple[str, str], list[Claim]] = {}
    replaced_by_slot: dict[tuple[str, str], list[Claim]] = {}
    review_states: dict[str, str] = {}
    review: list[ReviewItem] = []

    def slot_key(claim: Claim) -> tuple[str, str]:
        return (aliases.get(claim.subject.id, claim.subject.id), claim.predicate)

    unreadable: set[str] = set()

    # Proposals first: a correction may need the claim it is amending.
    for event in events:
        if event.type != "claim.proposed":
            continue
        parsed = claims_mod.parse(event)
        if isinstance(parsed, ClaimProblem):
            problems.append(parsed)
            unreadable.add(event.id)
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
        elif admission.state == SUPERSEDED:
            # The user corrected this reading. It is not a contender and never
            # was, but their correction is unauditable without it: a mistyped
            # 5mg for 50mg is undetectable once the original is gone.
            replaced_by_slot.setdefault(slot_key(parsed), []).append(parsed)
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
        dangling = isinstance(target_id, str) and bool(target_id) and target is None
        parsed = claims_mod.parse(event, target=target)
        if isinstance(parsed, ClaimProblem):
            if dangling:
                anomalies.append(
                    Anomaly(
                        f"{event.id}: correction targets {target_id}, which is not a "
                        f"proposed claim in this log, and does not name a subject and "
                        f"predicate of its own"
                    )
                )
            problems.append(parsed)
            continue
        if dangling:
            # Read on its own terms, and attached to the entity it names: this is
            # the page a reader would go to asking why the value looks the way it
            # does.
            anomalies.append(
                Anomaly(
                    f"{event.id}: correction targets {target_id}, which is not a proposed "
                    f"claim in this log; read on its own terms",
                    subject_id=parsed.subject.id,
                    cite=parsed.cite,
                )
            )
        review_states[parsed.event_id] = REVIEW_CONFIRMED
        admissions.append(
            Admission(parsed, ADMITTED, REVIEW_CONFIRMED, "your correction, which always stands")
        )
        admitted_by_slot.setdefault(slot_key(parsed), []).append(parsed)

    # Rule 3: a decision the rules could not act on is still a thing the user
    # said. A confirmation or rejection whose target is not in this log has no
    # subject and so no page to appear on, which makes the anomaly list its only
    # possible home — but it is never simply dropped.
    for target_id, decision in sorted(decisions.items(), key=lambda kv: kv[1].sort_key):
        if decision.type == "claim.corrected" or target_id in proposals:
            continue
        if target_id in unreadable:
            anomalies.append(
                Anomaly(
                    f"{decision.id}: you {_DECIDED[decision.type]} {target_id}, whose claim "
                    f"could not be read; the decision is recorded but applies to nothing"
                )
            )
        else:
            anomalies.append(
                Anomaly(
                    f"{decision.id}: you {_DECIDED[decision.type]} {target_id}, which is not "
                    f"a proposed claim in this log; the decision could not be applied"
                )
            )

    slots: dict[tuple[str, str], Slot] = {}
    for key in sorted(set(admitted_by_slot) | set(pending_by_slot) | set(replaced_by_slot)):
        subject_id, predicate = key
        slot, items = _resolve(
            subject_id,
            predicate,
            admitted_by_slot.get(key, []),
            pending_by_slot.get(key, []),
            review_states,
            replaced_by_slot.get(key, []),
        )
        # A slot with nothing admitted has no place in the wiki at all; its
        # pending claims are already in the review queue. A replaced reading is
        # different: it is the record of what a user act acted on, so a slot
        # carrying one is kept even when nothing currently holds the value.
        if slot.has_value or slot.readings or slot.superseded:
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
