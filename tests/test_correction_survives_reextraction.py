"""Phase 3 gate: a user's correction outranks a later re-extraction, either way round.

``CLAUDE.md`` calls this "the single most likely bug in the system". Replaying by
timestamp and letting the last write win reintroduces exactly the errors the user
has already fixed — and it does so silently, because the wiki looks perfectly
well formed afterwards.

So the same facts are asserted in **both** orderings: correction before the
re-extraction, and correction after it. The correction wins in each case, the
contradiction is raised for review in each case, and the value the model read is
kept as evidence rather than thrown away.
"""

from __future__ import annotations

import pytest

from agent import projection
from agent.projection import reconcile

from .conftest import claim, confirm, correct, ingested, on_day

DEVICE = "laptop-a1b2"
AS_OF = "2026-09-30T00:00:00Z"
SCRIPT_DATE = {"value": "2026-06-04", "precision": "day", "uncertainty_days": 0}


def _stream(correction_day: int, reextraction_day: int):
    """One misread dose, one correction, one re-extraction that disagrees."""
    misread = claim(
        DEVICE,
        "med:perindopril",
        "dose",
        {"amount": 4, "unit": "mg", "frequency": "daily"},
        ts=on_day(2),
        occurred=SCRIPT_DATE,
    )
    fix = correct(
        DEVICE,
        value={"amount": 5, "unit": "mg", "frequency": "daily"},
        target=misread.id,
        ts=on_day(correction_day),
    )
    # A better model reads the same photograph again and says something else.
    reextraction = claim(
        DEVICE,
        "med:perindopril",
        "dose",
        {"amount": 10, "unit": "mg", "frequency": "daily"},
        ts=on_day(reextraction_day),
        occurred=SCRIPT_DATE,
    )
    accepted = confirm(DEVICE, reextraction.id, ts=on_day(reextraction_day, hour=10))
    events = [ingested(DEVICE), misread, fix, reextraction, accepted]
    return sorted(events, key=lambda event: event.sort_key)


ORDERINGS = {
    "correction first": (3, 20),
    "re-extraction first": (20, 3),
}


@pytest.mark.parametrize("label", sorted(ORDERINGS))
def test_correction_survives_reextraction(label):
    correction_day, reextraction_day = ORDERINGS[label]
    result = projection.project(_stream(correction_day, reextraction_day), AS_OF)

    entity = result.entities["med:perindopril"]
    slot = entity.slots["dose"]

    assert slot.winner.value.literal == "5mg daily", (
        f"{label}: the correction lost to an extraction"
    )
    assert slot.winner.is_correction
    assert slot.resolution == reconcile.CONTRADICTED

    # The disagreeing reading is kept, not discarded: a human has to see what
    # the model said in order to decide anything about it.
    assert [c.value.literal for c in slot.contradicted_by] == ["10mg daily"]

    contradictions = [item for item in result.review if item.kind == "contradiction"]
    assert len(contradictions) == 1
    assert contradictions[0].subject_id == "med:perindopril"
    assert contradictions[0].consequence == "high"


@pytest.mark.parametrize("label", sorted(ORDERINGS))
def test_correction_reaches_the_page_in_both_orderings(label):
    correction_day, reextraction_day = ORDERINGS[label]
    result = projection.project(_stream(correction_day, reextraction_day), AS_OF)
    page = result.files["wiki/medications/perindopril.md"].decode("utf-8")

    assert "dose: 5mg daily" in page
    assert "\n## Needs review" in page
    assert "your correction stands" in page
    # The rejected reading appears only under review, never as the dose.
    assert "dose: 10mg daily" not in page
    assert "read instead as 10mg daily" in page


def test_both_orderings_produce_the_same_record():
    """The two streams differ only in when the user typed. The record must not."""
    first = projection.project(_stream(3, 20), AS_OF)
    second = projection.project(_stream(20, 3), AS_OF)

    assert first.entities["med:perindopril"].slots["dose"].winner.value.literal == (
        second.entities["med:perindopril"].slots["dose"].winner.value.literal
    )
    assert first.entities["med:perindopril"].status == second.entities["med:perindopril"].status


def test_a_correction_still_wins_when_the_reextraction_is_more_authoritative():
    """Evidence tier does not buy a way past a correction.

    Rule 2 (higher tier wins) is subordinate to rule 1 (corrections win). A
    correction is typed by the patient and carries the weakest tier there is; if
    tier were compared first, every re-extraction from a script would quietly
    overturn it.
    """
    misread = claim(
        DEVICE,
        "med:perindopril",
        "dose",
        "4mg daily",
        tier="prescriber-issued",
        ts=on_day(2),
        occurred=SCRIPT_DATE,
    )
    fix = correct(
        DEVICE, value="5mg daily", target=misread.id, ts=on_day(3), evidence_tier="patient-reported"
    )
    reextraction = claim(
        DEVICE,
        "med:perindopril",
        "dose",
        "10mg daily",
        tier="prescriber-issued",
        ts=on_day(20),
        occurred=SCRIPT_DATE,
    )
    accepted = confirm(DEVICE, reextraction.id, ts=on_day(21))
    events = sorted(
        [ingested(DEVICE), misread, fix, reextraction, accepted],
        key=lambda event: event.sort_key,
    )

    slot = projection.project(events, AS_OF).entities["med:perindopril"].slots["dose"]
    assert slot.winner.value.literal == "5mg daily"
    assert slot.winner.evidence_tier == "patient-reported"
