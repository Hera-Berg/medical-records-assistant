"""The queue, made actionable: what may be done to each item, and what that emits.

Shared by ``GET /api/review`` and ``POST /api/review/{event_id}`` so that the
list and the actions cannot disagree about what an item is. A screen offering a
button the route refuses is a screen that loses a user's decision, and rule 3
puts that among the worst outcomes available here.

Four things this module is careful about.

**The fold is re-derived server-side.** The path carries one claim's event id;
the item it belongs to is looked up in the current projection and *its* claims
are what get decided. One tap emits one ``claim.confirmed`` per claim, per
CLAUDE.md, and a client cannot name a set of targets of its own choosing.

**A stop proposal cannot be confirmed by the generic action.** ``confirm``
against :data:`~agent.projection.reconcile.STOP_PROPOSED` is refused with a
sentence naming the distinct action instead. The frontend renders a different
button; this is what makes that more than a rendering choice.

**What each kind may do differs, and the difference is a fact about the kind.**
Confirming is meaningless against a conflict — both readings are already
admitted, and agreeing with one again does not unseat the other. A conflict is
resolved by a correction, which outranks both, or by retracting the reading that
is wrong. So the actions are enumerated per kind rather than offered uniformly.

**Acting on an item somebody else already decided is an ordinary event**, not an
error. Two devices, or one tab left open while the other is used, hit it
routinely. :func:`already_decided` builds the sentence and the caller returns
the refreshed queue with it, so the user sees what happened and a list that is
now correct rather than a failed action to interpret.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..errors import HealthAgentError
from ..events import envelope
from ..events.envelope import Event
from ..projection import entities as entities_mod
from ..projection import reconcile, stops, subjects
from ..projection import unread as unread_mod
from ..projection.claims import Claim
from ..projection.reconcile import ReviewItem, Slot

# What a client may ask for. Named as acts rather than as verbs on a resource:
# "confirm this reading" and "take this medication off my list" are different
# things to have done, and the queue is the one place that distinction is worth
# more than the uniformity.
CONFIRM = "confirm"
CONFIRM_STOP = "confirm-stop"
REJECT = "reject"
CORRECT = "correct"
DATE = "date"
CONFIRM_AGAIN = "confirm-again"
KEEP_REJECTED = "keep-rejected"
DEALT_WITH = "dealt-with"

ACTIONS = frozenset(
    {CONFIRM, CONFIRM_STOP, REJECT, CORRECT, DATE, CONFIRM_AGAIN, KEEP_REJECTED, DEALT_WITH}
)

#: Which acts each review kind offers.
#:
#: ``conflict`` and ``contradiction`` deliberately have no ``confirm``. Their
#: claims are already admitted; the disagreement is between them, and endorsing
#: one again changes no ranking. What resolves them is a correction — which
#: outranks every extraction — or retracting the reading that is wrong.
#:
#: ``dateable`` has no dismissal. There is no event meaning "I do not know when
#: this was", and inventing one would either discard the phrase the record exists
#: to keep or silence the question while leaving it unanswered. It is a medium
#: item about a value that is already in the record, and it waits.
BY_KIND: Mapping[str, tuple[str, ...]] = {
    reconcile.AWAITING: (CONFIRM, CORRECT, REJECT),
    reconcile.STOP_PROPOSED: (CONFIRM_STOP, CORRECT, REJECT),
    reconcile.DATEABLE: (DATE, REJECT),
    reconcile.WITHDRAWN: (CONFIRM_AGAIN, KEEP_REJECTED),
    "conflict": (CORRECT, REJECT),
    "contradiction": (CORRECT, REJECT),
    entities_mod.STOP_REPORTED: (CORRECT,),
    reconcile.MERGE_PROPOSED: (),
    # Nothing was read, so there is nothing to confirm, correct or reject. The
    # person checks the document, photographs it again or types it in, and says
    # so. A new photograph is read afresh and raises its own item if it too
    # cannot be read.
    unread_mod.COULD_NOT_READ: (DEALT_WITH,),
}

#: Kinds where ``reject`` must name which claim it retracts.
#:
#: Retracting "the item" would retract both sides of a disagreement, which is
#: never what someone deciding a conflict means and would remove the evidence
#: that there was one.
PER_READING = frozenset({"conflict", "contradiction"})


class ReviewError(HealthAgentError):
    """An action that cannot be carried out, with the reason a person needs."""


@dataclass(frozen=True)
class Actionable:
    """One queue item, its identity for the route, and what may be done to it."""

    item: ReviewItem
    #: The event id this item is addressed by. Stable across a rebuild because
    #: it is a claim's own event id, not a position in a list.
    id: str
    slot: Slot | None
    name: str
    actions: tuple[str, ...]

    @property
    def kind(self) -> str:
        return self.item.kind

    @property
    def targets(self) -> tuple[str, ...]:
        """Every claim one tap decides. The fold, re-derived here and not sent."""
        return tuple(c.event_id for c in self.item.claims) or tuple(self.item.targets)

    @property
    def proposed(self) -> Claim | None:
        """The claim whose value the item is about, where there is one."""
        return self.item.claims[0] if self.item.claims else None

    @property
    def transitions(self) -> bool | None:
        """For a stop: whether confirming takes the medication off the list."""
        claim = self.proposed
        if self.kind != reconcile.STOP_PROPOSED or claim is None:
            return None
        return stops.transitions(claim)

    def allows(self, action: str) -> bool:
        return action in self.actions


def _display_name(item: ReviewItem, entities: Mapping[str, Any]) -> str:
    """What to call the subject on screen.

    The entity's name first, because that is what every other screen calls it.
    Then the source's own wording for the subject, which is the only rendered
    copy of what a label actually said. Then a name derived from the slug — a
    pending claim may have no entity at all, and "Sulfonamides" is a better
    thing to show a patient than ``allergy:sulfonamides``.
    """
    entity = entities.get(item.subject_id)
    if entity is not None and entity.name:
        return entity.name
    for claim in item.claims:
        if claim.subject_literal:
            return claim.subject_literal
    subject = subjects.parse(item.subject_id)
    return subjects.display_name(subject) if subject else item.subject_id


def queue(projection) -> tuple[Actionable, ...]:
    """Every waiting item, in the order the projection sorted them.

    High consequence first: that ordering is the projection's and is not
    re-decided here, so the inbox, the wiki and the CLI all agree about what is
    most urgent.
    """
    slots = projection.reconciliation.slots if projection.reconciliation else {}
    return tuple(
        Actionable(
            item=item,
            id=_identity(item),
            slot=slots.get((item.subject_id, item.predicate)),
            name=_display_name(item, projection.entities),
            actions=tuple(BY_KIND.get(item.kind, ())),
        )
        for item in projection.review
    )


def _identity(item: ReviewItem) -> str:
    """How the route addresses one item.

    A claim's own event id wherever there is one, so the address survives a
    rebuild and means the same thing on two devices. An item that carries
    neither claims nor targets still gets an id built from what it does name:
    it offers no actions, but it is listed, and an item nothing can address is
    an item the inbox would have to drop — which is the disappearance rule 3
    exists to stop.
    """
    if item.claims:
        return item.claims[0].event_id
    if item.targets:
        return item.targets[0]
    return f"{item.kind}:{item.subject_id}:{item.predicate}:{item.cite or ''}"


def find(projection, item_id: str) -> Actionable | None:
    """The item addressed by *item_id*, by any of the claims it folded.

    Any of them, not just the first: two devices looking at the same fold can
    have been served different ids for it if a re-extraction added a claim
    between their reads, and refusing the older id would turn an ordinary race
    into a decision the user has to make twice.
    """
    for entry in queue(projection):
        if item_id == entry.id or item_id in entry.targets:
            return entry
    return None


def already_decided(events: Sequence[Event], item_id: str) -> str:
    """Why *item_id* is no longer in the queue, said as a sentence.

    Written for someone who tapped a button on a list that had gone stale, which
    on a record synced between a laptop and a phone is an ordinary Tuesday
    rather than a fault. It names what happened to the thing they tapped, so the
    refreshed queue underneath it makes sense.
    """
    decided = _latest_decision(events, item_id)
    if decided is not None:
        when = _spoken_date(decided.ts)
        what = {
            "claim.confirmed": "confirmed",
            "claim.rejected": "rejected",
            "claim.corrected": "corrected",
            unread_mod.ACKNOWLEDGED: "marked as dealt with",
        }.get(decided.type, "decided")
        return (
            f"This was already {what}{when}, so it has left your review list. "
            f"Nothing was changed just now, and the list below is up to date."
        )
    return (
        "This is no longer waiting for a decision — it was either decided "
        "somewhere else or the record was rebuilt since this page was opened. "
        "Nothing was changed just now, and the list below is up to date."
    )


def _latest_decision(events: Iterable[Event], target: str) -> Event | None:
    latest: Event | None = None
    for event in events:
        if event.type not in (
            "claim.confirmed", "claim.rejected", "claim.corrected", unread_mod.ACKNOWLEDGED
        ):
            continue
        if event.payload.get("target") != target:
            continue
        if latest is None or event.sort_key > latest.sort_key:
            latest = event
    return latest


def _spoken_date(ts: str) -> str:
    """" on 8 September" from a timestamp, or nothing if it will not parse."""
    from ..projection.dates import MONTH_NAMES  # noqa: PLC0415 - fixed table

    moment = envelope.parse_ts_or_none(ts)
    if moment is None:
        return ""
    return f" on {moment.day} {MONTH_NAMES[moment.month - 1]}"


def build(
    entry: Actionable,
    action: str,
    device: str,
    value: str | None = None,
    occurred_at: Mapping[str, Any] | None = None,
    target: str | None = None,
) -> list[Event]:
    """The events one act appends, or a :class:`ReviewError` saying why not.

    Nothing is written here. The caller appends under the record's lock, which
    is what keeps a batch of decisions from interleaving with a capture.
    """
    if action not in ACTIONS:
        raise ReviewError(
            f"{action!r} is not something that can be done to a review item; "
            f"expected one of {', '.join(sorted(ACTIONS))}"
        )
    if not entry.allows(action):
        raise ReviewError(_refusal(entry, action))

    if action in (CONFIRM, CONFIRM_STOP, CONFIRM_AGAIN):
        return [
            envelope.new("claim.confirmed", device, payload={"target": event_id})
            for event_id in entry.targets
        ]
    if action in (REJECT, KEEP_REJECTED):
        return [
            envelope.new("claim.rejected", device, payload={"target": event_id})
            for event_id in _rejection_targets(entry, target)
        ]
    if action == CORRECT:
        return _corrections(entry, device, value)
    if action == DEALT_WITH:
        return [
            envelope.new(
                unread_mod.ACKNOWLEDGED,
                device,
                payload={"target": event_id, "artifact": entry.item.cite},
            )
            for event_id in entry.targets
        ]
    return _dating(entry, device, occurred_at)


def _refusal(entry: Actionable, action: str) -> str:
    """Why this act is not offered here, in words that name the one that is."""
    if entry.kind == reconcile.STOP_PROPOSED and action == CONFIRM:
        return (
            "this proposal would stop a medication, which is not something a "
            "general confirmation can do. Stopping is its own action so that a "
            "run of taps down a queue cannot drop a medication by accident — send "
            f"{CONFIRM_STOP!r} if that is what you mean"
        )
    if action in (CONFIRM, CONFIRM_STOP) and entry.kind in PER_READING:
        return (
            "there is nothing to confirm here: both readings are already in the "
            "record and agreeing with one again does not unseat the other. Correct "
            "the value, or reject the reading that is wrong"
        )
    offered = ", ".join(entry.actions) if entry.actions else "nothing yet"
    return (
        f"{action!r} is not something that can be done to a {entry.kind} item; "
        f"this one offers {offered}"
    )


def _rejection_targets(entry: Actionable, target: str | None) -> tuple[str, ...]:
    """Which claims a rejection retracts.

    A conflict must name one. Retracting the whole item would retract both
    sides of the disagreement, leaving no evidence that there was one and no
    way to tell which reading the user actually disbelieved.
    """
    if entry.kind not in PER_READING:
        return entry.targets
    if target is None:
        raise ReviewError(
            "this item holds more than one reading, so a rejection has to say "
            "which one is wrong; send the reading's own id as 'target'"
        )
    if target not in entry.targets:
        raise ReviewError(
            f"{target} is not one of the readings in this item, so rejecting it "
            f"here would retract something you are not looking at"
        )
    return (target,)


def _corrections(entry: Actionable, device: str, value: str | None) -> list[Event]:
    """One ``claim.corrected`` per claim the item folded.

    Symmetric with confirmation, and for the same reason: two documents that
    each misread the same dose are two readings that are each wrong, and a
    correction naming only one of them would leave the other in the queue
    asking about a fact the user has already settled.

    Corrections are the highest authority in the record and they outrank
    extractions regardless of order, so this is also how a conflict is resolved:
    the typed value wins over both readings and both stay visible underneath it.
    """
    text = (value or "").strip()
    if not text:
        raise ReviewError(
            "a correction has to say what the value should be; an empty "
            "correction would remove the reading without recording what replaced it"
        )
    if not entry.targets:
        raise ReviewError("there is no claim here to correct")
    return [
        envelope.new(
            "claim.corrected",
            device,
            payload={"target": event_id, "value": text},
        )
        for event_id in entry.targets
    ]


def _dating(
    entry: Actionable, device: str, occurred_at: Mapping[str, Any] | None
) -> list[Event]:
    """A date the user supplied, attached to the claim that had none.

    The value is restated verbatim from the claim being dated rather than
    re-typed or normalised: this act is about *when*, and a correction that
    quietly re-rendered ``5mg`` as ``5.0mg`` on its way past would change the
    record's answer to a question nobody asked.

    A candidate computed in code reaches the user as an offer and arrives back
    here as an ordinary date. Nothing in this path can apply one on its own.
    """
    claim = entry.proposed
    if claim is None:
        raise ReviewError("there is no claim here to date")
    if not isinstance(occurred_at, Mapping) or not occurred_at.get("value"):
        raise ReviewError(
            "a date has to say what it is; send occurred_at with a value, a "
            "precision and how many days either side it might be"
        )
    return [
        envelope.new(
            "claim.corrected",
            device,
            payload={
                "target": claim.event_id,
                "value": claim.value.literal,
                "occurred_at": dict(occurred_at),
            },
        )
    ]
