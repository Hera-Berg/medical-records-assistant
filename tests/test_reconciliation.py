"""The reconciliation rules, one test per rule.

Rule 1 (corrections outrank extractions) has its own file — it is a phase gate.
This covers the rest: evidence tiers, tie-breaking by date, contradiction without
resolution, merges, and what happens to a claim that cannot be read.
"""

from __future__ import annotations

from agent import projection
from agent.projection import reconcile

from .conftest import claim, confirm, correct, ingested, merge, on_day

DEVICE = "laptop-a1b2"
AS_OF = "2026-09-30T00:00:00Z"


def _project(events, as_of: str = AS_OF):
    return projection.project(sorted(events, key=lambda e: e.sort_key), as_of)


def _confirmed(subject, predicate, value, **kwargs):
    """A proposed claim plus the tap that admits it."""
    proposed = claim(DEVICE, subject, predicate, value, **kwargs)
    ts = kwargs.get("ts") or on_day(1)
    return [proposed, confirm(DEVICE, proposed.id, ts=ts)]


def test_conflicting_sources_render_both():
    """Two equal sources disagreeing is a visible state, not a silent pick."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _confirmed(
        "med:metformin", "dose", "500mg twice daily",
        ts=on_day(5), artifact="a3f91c", occurred={"value": "2026-07-01"},
    )
    events += _confirmed(
        "med:metformin", "dose", "850mg twice daily",
        ts=on_day(6), artifact="77b210", occurred={"value": "2026-07-01"},
    )

    result = _project(events)
    entity = result.entities["med:metformin"]
    slot = entity.slots["dose"]

    assert slot.resolution == reconcile.CONFLICTED
    assert slot.winner is None, "a conflict must not resolve to a value"
    assert {c.value.literal for c in slot.readings} == {"500mg twice daily", "850mg twice daily"}
    assert entity.status == "conflicted"

    page = result.files[entity.rel_path].decode("utf-8")
    assert "500mg twice daily" in page and "850mg twice daily" in page
    assert "## Conflicting sources" in page
    assert "neither has been chosen" in page
    # Nothing averaged, nothing picked.
    assert "675mg" not in page
    assert "dose: 500mg" not in page and "dose: 850mg" not in page

    conflicts = [item for item in result.review if item.kind == "conflict"]
    assert len(conflicts) == 1


def test_higher_evidence_tier_wins():
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _confirmed(
        "med:perindopril", "dose", "5mg daily", tier="prescriber-issued",
        ts=on_day(5), artifact="a3f91c", occurred={"value": "2026-07-01"},
    )
    events += _confirmed(
        "med:perindopril", "dose", "10mg daily", tier="patient-reported",
        ts=on_day(6), artifact="77b210", occurred={"value": "2026-07-01"},
    )

    slot = _project(events).entities["med:perindopril"].slots["dose"]
    assert slot.resolution == reconcile.SETTLED
    assert slot.winner.value.literal == "5mg daily"
    assert [c.value.literal for c in slot.superseded] == ["10mg daily"]


def test_a_later_script_within_a_tier_is_a_dose_change_not_a_conflict():
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _confirmed(
        "med:perindopril", "dose", "5mg daily", ts=on_day(5), artifact="a3f91c",
        occurred={"value": "2026-01-04"},
    )
    events += _confirmed(
        "med:perindopril", "dose", "10mg daily", ts=on_day(6), artifact="77b210",
        occurred={"value": "2026-06-04"},
    )

    result = _project(events)
    slot = result.entities["med:perindopril"].slots["dose"]
    assert slot.resolution == reconcile.SETTLED
    assert slot.winner.value.literal == "10mg daily"
    assert [c.value.literal for c in slot.superseded] == ["5mg daily"]
    assert not [item for item in result.review if item.kind == "conflict"]

    page = result.files["wiki/medications/perindopril.md"].decode("utf-8")
    assert "## Earlier readings" in page
    assert "5mg daily" in page, "the superseded reading is history, not deleted"


def test_dates_that_cannot_be_ordered_do_not_break_a_tie():
    """Overlapping uncertainty bands mean "we do not know which is later".

    Falling back to the nominal value here would turn an unknown ordering into a
    confident dose, which is the failure the fuzzy dates exist to prevent.
    """
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _confirmed(
        "med:perindopril", "dose", "5mg daily", ts=on_day(5), artifact="a3f91c",
        occurred={"value": "2026-04-05", "precision": "month", "uncertainty_days": 14},
    )
    events += _confirmed(
        "med:perindopril", "dose", "10mg daily", ts=on_day(6), artifact="77b210",
        occurred={"value": "2026-04-20", "precision": "day", "uncertainty_days": 0},
    )

    slot = _project(events).entities["med:perindopril"].slots["dose"]
    assert slot.resolution == reconcile.CONFLICTED
    assert slot.winner is None


def test_an_undated_claim_never_outranks_a_dated_one_by_default():
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _confirmed(
        "med:perindopril", "dose", "5mg daily", ts=on_day(5), artifact="a3f91c",
        occurred={"value": "2026-06-04"},
    )
    events += _confirmed(
        "med:perindopril", "dose", "10mg daily", ts=on_day(6), artifact="77b210",
    )
    slot = _project(events).entities["med:perindopril"].slots["dose"]
    assert slot.resolution == reconcile.CONFLICTED


def test_merges_are_events_and_reverts_undo_them():
    events = [ingested(DEVICE)]
    events += _confirmed("med:panadol", "dose", "500mg as needed", ts=on_day(2),
                         tier="patient-reported")
    events += _confirmed("med:paracetamol", "dose", "500mg as needed", ts=on_day(3),
                         tier="patient-reported")
    merged = events + [merge(DEVICE, "med:panadol", "med:paracetamol", ts=on_day(4))]

    result = _project(merged)
    assert result.entities["med:panadol"].merged_into == "med:paracetamol"
    assert "med:panadol" in result.entities["med:paracetamol"].merged_from
    stub = result.files["wiki/medications/panadol.md"].decode("utf-8")
    assert "merged_into: med:paracetamol" in stub
    # The stub cites the merge decision itself, which is the line to revert.
    assert "Your merge decision" in stub
    assert result.unresolved_citations == ()

    reverted = merged + [
        merge(DEVICE, "med:panadol", "med:paracetamol", ts=on_day(5), reverted=True)
    ]
    after = _project(reverted)
    assert after.entities["med:panadol"].merged_into is None
    assert after.entities["med:panadol"].slots["dose"].winner is not None


def test_the_latest_decision_on_a_claim_is_the_one_that_counts():
    proposed = claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(1))
    events = [
        ingested(DEVICE),
        proposed,
        confirm(DEVICE, proposed.id, ts=on_day(2)),
        correct(DEVICE, value="7.5mg daily", target=proposed.id, ts=on_day(3)),
    ]
    slot = _project(events).entities["med:perindopril"].slots["dose"]
    assert slot.winner.value.literal == "7.5mg daily"
    assert slot.winner.is_correction


def test_a_malformed_claim_is_reported_and_never_coerced():
    """Validate, then reject. Never repair."""
    events = [
        ingested(DEVICE),
        claim(DEVICE, "med:perindopril", "dose", {}, ts=on_day(1)),
        claim(DEVICE, "med:perindopril", "dose", "5mg daily", tier="made-up", ts=on_day(2)),
        claim(DEVICE, "not-a-subject", "dose", "5mg daily", ts=on_day(3)),
        claim(DEVICE, "med:../../etc/passwd", "dose", "5mg daily", ts=on_day(4)),
        claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(5),
              occurred={"value": "the Tuesday after Easter"}),
    ]
    result = _project(events)

    assert len(result.problems) == 5
    assert result.entities == {}
    reasons = " ".join(problem.reason for problem in result.problems)
    assert "unusable slug" in reasons
    assert "unknown kind" in reasons or "no kind prefix" in reasons
    assert "evidence_tier" in reasons
    assert "written as null rather than approximated" in reasons


def test_a_claim_about_an_unknown_predicate_still_gates_high():
    """An unrecognised predicate falls back to high, and says so."""
    proposed = claim(DEVICE, "med:perindopril", "sparkliness", "very", ts=on_day(1))
    result = _project([ingested(DEVICE), proposed])
    assert result.entities == {}
    assert result.review[0].consequence == "high"


def test_a_decision_naming_no_target_is_reported_not_dropped():
    """Rule 3: a user act the rules cannot apply still has to be findable.

    A confirmation or rejection with no target has no subject and so no page to
    appear on, which leaves the anomaly list as its only possible home. It is
    still not allowed to vanish — the user tapped something.
    """
    from agent.events import envelope

    events = [ingested(DEVICE, "a3f91c")]
    events += _confirmed("med:metformin", "dose", "500mg twice daily", ts=on_day(5))
    blank = envelope.new("claim.confirmed", DEVICE, payload={}, ts=on_day(6))
    events.append(blank)

    result = _project(events)
    assert any(blank.id in note and "names no target" in note for note in result.anomalies)


def test_a_decision_on_a_claim_that_is_not_here_is_reported():
    """Hand-edited logs and half-synced shards both produce this, and it is a
    decision the user made that the record cannot honour."""
    from agent.events import envelope

    events = [ingested(DEVICE, "a3f91c")]
    events += _confirmed("med:metformin", "dose", "500mg twice daily", ts=on_day(5))
    orphan = envelope.new(
        "claim.rejected", DEVICE, payload={"target": "01J0000000000000000000MISS"},
        ts=on_day(6),
    )
    events.append(orphan)

    result = _project(events)
    notes = [n for n in result.anomalies if orphan.id in n]
    assert notes and "rejected" in notes[0] and "not a proposed claim" in notes[0]


def test_a_decision_on_an_unreadable_claim_is_reported():
    """The claim is already in `problems`; the tap on it would otherwise vanish."""
    from agent.events import envelope

    broken = envelope.new(
        "claim.proposed", DEVICE, actor="agent",
        payload={"subject": "med:metformin", "predicate": "dose"}, ts=on_day(5),
    )
    events = [ingested(DEVICE, "a3f91c"), broken, confirm(DEVICE, broken.id, ts=on_day(6))]

    result = _project(events)
    assert result.problems
    assert any("could not be read" in note for note in result.anomalies)


def test_endorsement_survives_being_outranked():
    """`Slot.endorsed` records the tap before ranking, which is what buries it."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    weak = claim(
        DEVICE, "med:metformin", "dose", "500mg twice daily", ts=on_day(5),
        tier="patient-reported", occurred={"value": "2026-07-01"},
    )
    events += [weak, confirm(DEVICE, weak.id, ts=on_day(5, hour=10))]
    events += _confirmed(
        "med:metformin", "dose", "850mg twice daily", ts=on_day(6),
        artifact="77b210", occurred={"value": "2026-07-02"},
    )

    result = _project(events)
    slot = result.entities["med:metformin"].slots["dose"]

    assert slot.winner.value.literal == "850mg twice daily"
    assert weak.id in slot.endorsed
    assert weak.id in {c.event_id for c in slot.endorsed_claims()}
    # And it is still on the page, under earlier readings.
    assert "500mg twice daily" in result.files[
        result.entities["med:metformin"].rel_path
    ].decode("utf-8")
