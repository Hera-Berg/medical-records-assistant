"""The review inbox: what is waiting on a person, and the taps that decide it.

This is the one route in the application that lets a high-consequence claim into
the record, so almost everything here is about making that deliberate.

**Nothing is decided by this route.** It appends user events — ``claim.confirmed``,
``claim.rejected``, ``claim.corrected`` — and the projection reduces them on the
next read. There is no path from here to ``wiki/``; a confirmation that the
reconciliation rules decline to act on stays visible in the queue rather than
silently taking effect, which is rule 3 working as intended rather than a bug in
this file.

**The fold is re-derived here, never sent by the client.** One tap on a fact two
documents agree about emits one ``claim.confirmed`` per claim, and which claims
those are is read out of the current projection.

**Stopping a medication is its own action.** ``confirm`` against a stop proposal
is refused with a sentence, not rendered differently and hoped for.

**A stale decision is an ordinary event.** Two devices, or one tab left open,
routinely act on a queue that has moved. The answer says what happened to the
item in plain words and carries the refreshed queue with it, so the user is
looking at the truth rather than at a failed request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ...projection import reconcile
from .. import review as review_mod, serialise
from ..deps import get_state
from ..serving import API_HEADERS
from ..state import RecordState

router = APIRouter()

#: The order the inbox is worked in. High first, always: an unreviewed allergy
#: outranks a practitioner's job title however long the latter has waited.
TIERS = ("high", "medium", "low")


def _queue_payload(state: RecordState) -> dict[str, Any]:
    """The whole inbox, grouped by consequence tier.

    Rebuilt from a fresh snapshot every time, including in the answer to a
    decision, so a client never has to guess what its own tap did.
    """
    snapshot = state.snapshot()
    entries = review_mod.queue(snapshot.projection)
    grouped: dict[str, list[dict[str, Any]]] = {tier: [] for tier in TIERS}
    for entry in entries:
        grouped.setdefault(entry.item.consequence, []).append(
            serialise.inbox_item(entry, snapshot.citer)
        )
    return {
        "counts": serialise.review_counts(snapshot.projection.review),
        "tiers": grouped,
        "actionable": True,
        # Anomalies are things the record noticed about its own filing. CLAUDE.md
        # requires the inbox to report the count so they cannot scroll past
        # unseen; the sentences come with it, because a number whose detail is
        # somewhere else is a number nobody reads.
        "anomalies": {
            "count": snapshot.anomaly_count,
            "items": list(snapshot.anomalies()),
        },
        "as_of": snapshot.built_ts,
    }


@router.get("/api/review")
def review(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    """Everything waiting on a person, with what each item would change.

    Unlike every other read route, this one sends proposed values. That is the
    point of the screen: a queue that asks for a tap without showing what the
    tap agrees to is asking someone to sign an unread document. Rejected content
    is still absent — a rejected reading raises no item, and a withdrawal
    carries no claims — so there is nothing here to filter.
    """
    return _queue_payload(state)


@router.post("/api/review/{event_id}")
def decide(
    event_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
    state: RecordState = Depends(get_state),
):
    """Confirm, correct or reject one item — or say a stop is genuinely meant.

    The whole decision is taken under the record's lock: the queue is re-read,
    the item is found, its events are built and appended together. Without that,
    two taps arriving at once could each read a queue the other was about to
    change.
    """
    action = str(body.get("action") or "").strip()

    with state.lock:
        snapshot = state.snapshot()
        entry = review_mod.find(snapshot.projection, event_id)
        if entry is None:
            # Not an error the user has to interpret. Something they tapped had
            # already been decided — most likely by them, on another device —
            # so the answer says so and hands back a queue that is correct.
            payload = _queue_payload(state)
            payload["decided"] = {
                "id": event_id,
                "message": review_mod.already_decided(snapshot.events, event_id),
            }
            return JSONResponse(status_code=409, content=payload, headers=API_HEADERS)

        events = review_mod.build(
            entry,
            action,
            device=state.vault.identity.id,
            value=body.get("value"),
            occurred_at=body.get("occurred_at"),
            target=body.get("target"),
        )
        for event in events:
            state.append(event)

    payload = _queue_payload(state)
    payload["decided"] = {
        "id": entry.id,
        "action": action,
        "kind": entry.kind,
        "subject_id": entry.item.subject_id,
        "name": entry.name,
        # How many claims this one tap decided. Said back rather than assumed:
        # "confirmed, from both documents" is the sentence that explains why the
        # second document did not come back to ask again.
        "events": [event.id for event in events],
        "claims_decided": len(events),
        "message": _spoken_outcome(entry, action, len(events)),
    }
    return JSONResponse(status_code=200, content=payload, headers=API_HEADERS)


def _spoken_outcome(entry, action: str, count: int) -> str:
    """What just happened, in the words a patient would use.

    A stop that annotates rather than transitions says so here as well as in the
    queue. The user tapped a button that said the medication stays on the list,
    and being told again that it did is what makes the two agree.
    """
    sources = "" if count <= 1 else f", from {count} documents"
    if action == review_mod.CONFIRM_STOP:
        if entry.transitions:
            return f"{entry.name} has been taken off your medication list{sources}."
        return (
            f"Recorded that {entry.name} was stopped{sources}. It stays on your "
            f"medication list, because only a prescription or a lab result can "
            f"take it off — a clinician seeing both is more use than either alone."
        )
    if action == review_mod.CONFIRM:
        return f"Confirmed{sources}. It is in your record now."
    if action in (review_mod.REJECT, review_mod.KEEP_REJECTED):
        return "Rejected. It is not in your record, and nothing shows what it said."
    if action == review_mod.CONFIRM_AGAIN:
        return f"Confirmed again{sources}. It is back in your record."
    if action == review_mod.CORRECT:
        return f"Corrected{sources}. Your version is what the record shows from now on."
    if action == review_mod.DATE:
        return "Dated. The timeline can place it now."
    return "Recorded."


#: Kinds the inbox knows how to render. Exported so a test can assert that every
#: kind the projection can raise has somewhere to go — a queue item no screen
#: renders is a user act with nowhere to be seen.
RENDERED_KINDS = frozenset(review_mod.BY_KIND) | {reconcile.MERGE_PROPOSED}
