"""Generated views. Returned to a caller, never written to the vault.

"Current medications" is a **generated view, not a stored file". Writing it out
would create a second place where the medication list lives, and the moment two
copies exist one of them is wrong. The API serves this from the projection; the
folder keeps one file per entity and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from . import entities as entities_mod
from .entities import Entity

#: Statuses that keep a medication on the current list. ``stale`` is included
#: deliberately: absence of evidence is not evidence of absence, and a stale
#: entry is exactly the one a clinician needs to ask about.
CURRENT_STATUSES = (entities_mod.ACTIVE, entities_mod.STALE, entities_mod.CONFLICTED)


@dataclass(frozen=True)
class MedicationRow:
    """One line of the current medications view."""

    id: str
    name: str
    status: str
    dose: str | None
    evidence_tier: str | None
    last_confirmed: str | None
    expected_exhaustion: str | None
    stale: bool
    conflicted: bool
    #: Set when the patient reported stopping this and the tier rule declined to
    #: act on it. The row stays on the list; the clinician reading it sees both
    #: what was prescribed and what the patient says they take.
    stop_reported: str | None
    stop_reported_tier: str | None
    sources: tuple[str, ...]
    #: Every distinct dose a source states, when they disagree. Empty otherwise.
    #:
    #: A conflicted slot has no winner, so ``dose`` above is ``None`` — and a
    #: medication list that renders that as a blank is telling a reader the dose
    #: is unknown when in fact two documents state it and disagree. That is the
    #: silent pick rule 3 forbids, arrived at by omission instead of by choosing.
    #: Both readings travel with the row so the list can show both, as the
    #: entity page does.
    dose_readings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "dose": self.dose,
            "evidence_tier": self.evidence_tier,
            "last_confirmed": self.last_confirmed,
            "expected_exhaustion": self.expected_exhaustion,
            "stale": self.stale,
            "conflicted": self.conflicted,
            "stop_reported": self.stop_reported,
            "stop_reported_tier": self.stop_reported_tier,
            "sources": list(self.sources),
            "dose_readings": list(self.dose_readings),
        }


def current_medications(entities: Mapping[str, Entity]) -> tuple[MedicationRow, ...]:
    """Every medication the record still carries, stale and conflicted included."""
    rows: list[MedicationRow] = []
    for subject_id in sorted(entities):
        entity = entities[subject_id]
        if entity.subject.kind != "med" or entity.is_stub:
            continue
        if entity.status not in CURRENT_STATUSES:
            continue
        dose_slot = entity.slots.get("dose")
        dose = (
            dose_slot.winner.value.literal
            if dose_slot is not None and dose_slot.winner is not None
            else None
        )
        readings: tuple[str, ...] = ()
        if dose_slot is not None and dose_slot.is_conflicted:
            # Ordered by the claim's own sort key, not by value, so the list
            # reads the same on every machine and does not imply a ranking
            # between two readings the projection declined to rank.
            seen: list[str] = []
            for claim in dose_slot.readings:
                if claim.value.literal not in seen:
                    seen.append(claim.value.literal)
            readings = tuple(seen)
        rows.append(
            MedicationRow(
                id=entity.id,
                name=entity.name,
                status=entity.status,
                dose=dose,
                evidence_tier=entity.evidence_tier,
                last_confirmed=entity.last_confirmed.iso if entity.last_confirmed else None,
                expected_exhaustion=(
                    entity.expected_exhaustion.iso if entity.expected_exhaustion else None
                ),
                stale=entity.stale,
                conflicted=entity.status == entities_mod.CONFLICTED,
                stop_reported=(
                    entity.stop_report.iso if entity.stop_report is not None else None
                ),
                stop_reported_tier=(
                    entity.stop_report.tier if entity.stop_report is not None else None
                ),
                sources=entity.sources,
                dose_readings=readings,
            )
        )
    return tuple(rows)
