"""The patient profile: explicit user edits, preserved as events."""
from __future__ import annotations

from typing import Any
from datetime import date
from fastapi import APIRouter, Body, Depends, HTTPException
from ... import profile
from ...events import envelope
from ..deps import get_state
from ..state import RecordState
from .. import serialise

router = APIRouter()


def listing(state):
    snapshot = state.snapshot()
    result = profile.read(snapshot.events)
    result["sections"] = profile.SECTIONS
    result["age"] = None
    try:
        born = date.fromisoformat(result["values"]["date_of_birth"])
        today = state.now().date()
        if born <= today:
            result["age"] = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    except ValueError:
        pass
    clinical = {"allergies": [], "medications": [], "problems": []}
    for entity in sorted(snapshot.projection.entities.values(), key=lambda item: item.name):
        # A pending-only entity must not read as an established diagnosis or allergy.
        if entity.is_stub or not any(slot.winner or slot.is_conflicted for slot in entity.slots.values()):
            continue
        kind = entity.subject.kind
        group = {"allergy": "allergies", "med": "medications", "problem": "problems"}.get(kind)
        if not group or (kind == "med" and entity.status == "stopped"):
            continue
        item = serialise.entity_summary(entity, snapshot.as_of.date())
        for predicate in ("reaction", "severity"):
            slot = entity.slots.get(predicate)
            item[predicate] = slot.winner.value.literal if slot and slot.winner and not slot.is_conflicted else None
        clinical[group].append(item)
    result["clinical"] = clinical
    result["review_count"] = len(snapshot.projection.review)
    return result


@router.get("/api/profile")
def get_profile(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    return listing(state)


@router.put("/api/profile")
def update_profile(body: dict[str, Any] = Body(...), state: RecordState = Depends(get_state)) -> dict[str, Any]:
    if set(body) != {"values", "revision"}:
        raise HTTPException(400, "Send profile values and the revision you opened.")
    try:
        values = profile.validate(body["values"], state.now().date())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    with state.lock:
        current = listing(state)
        if body["revision"] != current["revision"]:
            raise HTTPException(409, "This profile changed in another window or device. Close the editor, reload the profile, and try again. Your changes have not been saved.")
        changes = {key: value for key, value in values.items() if value != current["values"][key]}
        if changes:
            state.append(envelope.new(profile.UPDATED, state.vault.identity.id,
                         actor="user", payload={"values": changes}, ts=envelope.format_ts(state.now())))
        return listing(state)
