"""``/api/doctor-notes`` — the locked compartment, as ciphertext in and out.

The browser seals and opens; this route stores and lists. It never receives a
passphrase or a note's words, so it has nothing to redact and nothing to leak,
and the rules about what may reach the frontend or the logs hold here by there
being nothing to hold them against. See :mod:`agent.sealed`.

**One compartment per record.** A second passphrase would split the notes in
two with nothing saying which half a reader is missing. Two devices creating
one at the same moment before either has seen the other's is still possible
in a synced folder; both are listed, oldest first, and the page says how many
notes it could not open with the passphrase given.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ... import sealed
from ...events import envelope
from ..deps import get_state
from ..state import RecordState

router = APIRouter()


def _listing(state: RecordState) -> dict[str, Any]:
    compartments, notes = sealed.read(state.snapshot().events)
    return {
        "compartments": [
            {
                "id": c.id,
                "ts": c.ts,
                "device": c.device,
                "kdf": c.kdf,
                "check": c.check,
            }
            for c in compartments
        ],
        # Newest first, as the timeline is.
        "notes": [
            {
                "event_id": n.event_id,
                "ts": n.ts,
                "device": n.device,
                "compartment": n.compartment,
                "iv": n.iv,
                "ciphertext": n.ciphertext,
            }
            for n in reversed(notes)
        ],
    }


@router.get("/api/doctor-notes")
def list_notes(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    return _listing(state)


@router.post("/api/doctor-notes/compartment", status_code=201)
def create_compartment(
    body: dict[str, Any] = Body(...),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    compartments, _ = sealed.read(state.snapshot().events)
    if compartments:
        raise HTTPException(
            status_code=409,
            detail="This record already has a passphrase for doctor's notes. Open it with that one.",
        )
    try:
        payload = sealed.compartment_payload(body)
    except sealed.SealedPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    event = envelope.new(
        sealed.COMPARTMENT_CREATED, state.vault.identity.id, actor="user", payload=payload
    )
    state.append(event)
    return _listing(state)


@router.post("/api/doctor-notes", status_code=201)
def add_note(
    body: dict[str, Any] = Body(...),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    compartments, _ = sealed.read(state.snapshot().events)
    try:
        payload = sealed.note_payload(body, compartments)
    except sealed.SealedPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    event = envelope.new(
        sealed.NOTE_SEALED, state.vault.identity.id, actor="user", payload=payload
    )
    state.append(event)
    return _listing(state)
