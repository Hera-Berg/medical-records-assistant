"""Staleness, and the rule that nothing disappears for want of being mentioned.

Rule 4: "Absence of evidence is never evidence of absence. Nothing is removed
from the active medication list because it stopped being mentioned." A supply
that should have run out becomes ``stale`` and says how long it has been; it
stays on the list, and on the generated current-medications view, where a
clinician can see it and ask about it.

The only thing that moves a medication to ``stopped`` is an explicit statement by
the user.
"""

from __future__ import annotations

import pytest

from agent import projection
from agent.projection import entities as entities_mod

from .conftest import claim, confirm, correct, ingested, on_day

DEVICE = "laptop-a1b2"
SCRIPT = {"value": "2026-06-04", "precision": "day", "uncertainty_days": 0}
THIRTY_DAYS = {"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"}


def _med(as_of: str, dispense=THIRTY_DAYS, occurred=SCRIPT, extra=()):
    proposed = claim(
        DEVICE, "med:perindopril", "dose", "5mg daily",
        ts=on_day(2), occurred=occurred, dispense=dispense,
    )
    events = [ingested(DEVICE), proposed, confirm(DEVICE, proposed.id, ts=on_day(2, hour=10))]
    events.extend(extra)
    return projection.project(sorted(events, key=lambda e: e.sort_key), as_of)


def test_no_silent_drop():
    """A medication with evidence never disappears; it becomes stale."""
    result = _med("2027-06-01T00:00:00Z")
    entity = result.entities["med:perindopril"]

    assert entity.status == entities_mod.STALE
    assert entity.stale is True
    assert entity.slots["dose"].winner.value.literal == "5mg daily"

    # Still on the current list, which is the whole point.
    current = [row.id for row in result.current_medications]
    assert current == ["med:perindopril"]

    page = result.files[entity.rel_path].decode("utf-8")
    assert "status: stale" in page
    assert "marked stale" in page
    assert "last confirmed" in page


def test_the_expected_exhaustion_date_is_computed_from_the_literal_spans():
    result = _med("2026-06-10T00:00:00Z")
    entity = result.entities["med:perindopril"]

    assert entity.expected_exhaustion.iso == "2026-07-04"
    assert entity.stale is False
    assert entity.status == entities_mod.ACTIVE

    page = result.files[entity.rel_path].decode("utf-8")
    assert "30 tablets; one daily; no repeats" in page
    assert "30 days of supply" in page


@pytest.mark.parametrize(
    "as_of,expected_stale",
    [
        ("2026-07-03T00:00:00Z", False),
        ("2026-07-04T00:00:00Z", False),
        ("2026-07-05T00:00:00Z", True),
    ],
)
def test_staleness_begins_only_after_the_supply_runs_out(as_of, expected_stale):
    assert _med(as_of).entities["med:perindopril"].stale is expected_stale


def test_a_fuzzy_script_date_makes_a_fuzzy_exhaustion_date():
    """An uncertain start cannot produce a certain end.

    The medication is not marked stale until ``as_of`` is past the *late* end of
    the band, so the fuzz always errs towards leaving a medication alone rather
    than ageing it out on a date the record never established.
    """
    occurred = {"value": "2026-06-04", "precision": "month", "uncertainty_days": 14}
    assert _med("2026-07-20T00:00:00Z", occurred=occurred).entities["med:perindopril"].stale is False
    assert _med("2026-09-20T00:00:00Z", occurred=occurred).entities["med:perindopril"].stale is True


def test_later_evidence_clears_staleness():
    refill = claim(
        DEVICE, "med:perindopril", "dose", "5mg daily",
        ts=on_day(20), occurred={"value": "2026-09-01"}, dispense=THIRTY_DAYS,
    )
    result = _med(
        "2026-09-10T00:00:00Z",
        extra=[refill, confirm(DEVICE, refill.id, ts=on_day(20, hour=10))],
    )
    entity = result.entities["med:perindopril"]
    assert entity.stale is False
    assert entity.last_confirmed.iso == "2026-09-01"


@pytest.mark.parametrize(
    "dispense",
    [
        None,
        {"quantity": "30 tablets", "frequency": "as needed", "repeats": "no repeats"},
        {"quantity": "a shoebox full", "frequency": "one daily", "repeats": "no repeats"},
        {"quantity": "30 tablets", "frequency": "one daily"},
    ],
)
def test_an_uncountable_supply_produces_no_exhaustion_date_and_no_staleness(dispense):
    """No date is better than a guessed one: a guess would age the entry out."""
    result = _med("2030-01-01T00:00:00Z", dispense=dispense)
    entity = result.entities["med:perindopril"]

    assert entity.expected_exhaustion is None
    assert entity.stale is False
    assert entity.status == entities_mod.ACTIVE
    # Omitted, not written as null. A derived file has nothing to distinguish
    # between "looked and found none" and "never asked", so the key that would
    # carry the distinction is only noise — see `Document.optional_field`.
    assert b"expected_exhaustion" not in result.files[entity.rel_path]


def test_only_an_explicit_user_statement_stops_a_medication():
    stop = claim(DEVICE, "med:perindopril", "status", "stopped", ts=on_day(20),
                 tier="patient-reported")
    # Proposed but untouched: high consequence, so it changes nothing.
    unconfirmed = _med("2026-09-30T00:00:00Z", extra=[stop])
    assert unconfirmed.entities["med:perindopril"].status != entities_mod.STOPPED

    # Corrected by the user: stopped.
    corrected = _med(
        "2026-09-30T00:00:00Z",
        extra=[stop, correct(DEVICE, value="stopped", target=stop.id, ts=on_day(21))],
    )
    entity = corrected.entities["med:perindopril"]
    assert entity.status == entities_mod.STOPPED
    # The file stays, with its history.
    assert entity.rel_path in corrected.files
    assert b"5mg daily" in corrected.files[entity.rel_path]
    assert not corrected.current_medications


def test_status_precedence_never_hides_a_state():
    """A medication can be several things at once; the fields say all of them."""
    other = claim(
        DEVICE, "med:perindopril", "dose", "10mg daily", ts=on_day(3),
        occurred=SCRIPT, artifact="77b210",
    )
    result = _med(
        "2027-06-01T00:00:00Z",
        extra=[ingested(DEVICE, "77b210"), other, confirm(DEVICE, other.id, ts=on_day(3, hour=10))],
    )
    entity = result.entities["med:perindopril"]

    assert entity.status == entities_mod.CONFLICTED  # the more urgent of the two
    assert entity.stale is True                      # and the other one is still recorded
    page = result.files[entity.rel_path].decode("utf-8")
    assert "status: conflicted" in page
    assert "stale: true" in page
    assert "conflicts:" in page


@pytest.mark.parametrize(
    "tier, stops",
    [
        ("prescriber-issued", True),
        ("lab-issued", True),
        ("device-recorded", False),
        ("patient-reported", False),
        ("inferred", False),
    ],
)
def test_a_confirmed_stop_needs_a_prescriber_or_lab_source(tier, stops):
    """Confirming is one tap, so the reading behind it has to be able to cease a drug.

    ``patient-reported`` and ``inferred`` can never produce a stop, however
    emphatically they are confirmed — a careless tap must not be able to drop a
    medication. The user saying they stopped something is a correction to type,
    not a proposal to accept; the correction path below is unconstrained.
    """
    stop = claim(DEVICE, "med:perindopril", "status", "stopped", ts=on_day(20), tier=tier)
    result = _med(
        "2026-09-30T00:00:00Z",
        extra=[stop, confirm(DEVICE, stop.id, ts=on_day(21))],
    )
    entity = result.entities["med:perindopril"]

    assert (entity.status == entities_mod.STOPPED) is stops
    # Whichever way it went, the medication is still on file with its history.
    assert entity.rel_path in result.files
    assert b"5mg daily" in result.files[entity.rel_path]
    # Declining to act on the tap is legitimate; losing it is not.
    assert (entity.stop_report is not None) is not stops


def test_the_tier_constraint_does_not_reach_the_correction_path():
    """A ``claim.corrected`` stop stands at any tier — the user typed the value.

    The constraint guards the tap, not the user. Correcting a medication to
    stopped is the deliberate act the consequence table asks for, and there is no
    extractor's reading to vouch for.
    """
    stop = claim(DEVICE, "med:perindopril", "status", "stopped", ts=on_day(20),
                 tier="inferred")
    result = _med(
        "2026-09-30T00:00:00Z",
        extra=[stop, correct(DEVICE, value="stopped", target=stop.id, ts=on_day(21))],
    )
    assert result.entities["med:perindopril"].status == entities_mod.STOPPED


def _reported_stop(tier="patient-reported", as_of="2026-09-30T00:00:00Z", extra=()):
    stop = claim(
        DEVICE, "med:perindopril", "status", "stopped", ts=on_day(20), tier=tier,
        occurred={"value": "2026-09-20", "precision": "day", "uncertainty_days": 0},
    )
    return _reported_stop_result(stop, as_of, extra), stop


def _reported_stop_result(stop, as_of, extra):
    return _med(as_of, extra=[stop, confirm(DEVICE, stop.id, ts=on_day(21))] + list(extra))


def test_a_declined_stop_annotates_and_is_never_invisible():
    """The whole of rule 3's second paragraph, on the path that motivated it.

    Status does not transition, and the tap is nonetheless findable in all three
    places the rule names: frontmatter, body prose, and the review queue.
    """
    result, stop = _reported_stop()
    entity = result.entities["med:perindopril"]

    assert entity.status == entities_mod.ACTIVE
    assert entity.stop_report is not None
    assert entity.stop_report.tier == "patient-reported"
    assert entity.stop_report.iso == "2026-09-20"

    page = result.files[entity.rel_path].decode("utf-8")
    assert "status: active" in page
    assert "stop_reported: 2026-09-20" in page
    assert "stop_reported_tier: patient-reported" in page
    assert "## Reported stopped" in page
    assert "saying this was stopped on 20 September 2026" in page

    # A sentence in the wiki without a footnote is a bug, this one included.
    stop_lines = [line for line in page.splitlines() if "was stopped on" in line]
    assert stop_lines and all("[^" in line for line in stop_lines)

    items = [item for item in result.review if item.kind == entities_mod.STOP_REPORTED]
    assert len(items) == 1
    assert items[0].subject_id == "med:perindopril"
    assert items[0].claims[0].event_id == stop.id
    # High-consequence, so it sorts to the front of the inbox with the rest.
    assert items[0].consequence == "high"
    assert result.review[0].kind == entities_mod.STOP_REPORTED


def test_the_page_never_states_both_active_and_stopped_flatly():
    """A bare "Status — stopped" bullet under `status: active` is a contradiction.

    The reported-stop section says the same thing with the context that makes it
    true, so the bullet stands down rather than being printed beside it.
    """
    result, _ = _reported_stop()
    page = result.files[result.entities["med:perindopril"].rel_path].decode("utf-8")

    assert "**Status** — stopped" not in page
    assert "## Reported stopped" in page


def test_a_reported_stop_is_a_discrepancy_not_a_conflict():
    """`conflicted` stays reserved for contradictory sources for the same fact."""
    result, _ = _reported_stop()
    entity = result.entities["med:perindopril"]

    assert entity.status != entities_mod.CONFLICTED
    assert entity.conflicts == ()
    assert not any(item.kind == "conflict" for item in result.review)


def test_a_reported_stop_stays_on_the_current_medications_view():
    """The list a clinician reads carries both facts, not just the prescribed one."""
    result, _ = _reported_stop()
    row = result.current_medications[0]

    assert row.id == "med:perindopril"
    assert row.status == entities_mod.ACTIVE
    assert row.dose == "5mg daily"
    assert row.stop_reported == "2026-09-20"
    assert row.stop_reported_tier == "patient-reported"
    assert row.to_dict()["stop_reported_tier"] == "patient-reported"


def test_an_outranked_stop_is_still_reported():
    """Being outranked must not be the same as being forgotten.

    A prescriber-issued ``status`` reading beats the patient's on tier and takes
    the slot's winner, which is where a naive implementation would stop looking.
    """
    keep = claim(
        DEVICE, "med:perindopril", "status", "active", ts=on_day(22),
        tier="prescriber-issued", artifact="77b210",
        occurred={"value": "2026-09-22", "precision": "day", "uncertainty_days": 0},
    )
    result, stop = _reported_stop(
        extra=[ingested(DEVICE, "77b210"), keep, confirm(DEVICE, keep.id, ts=on_day(23))]
    )
    entity = result.entities["med:perindopril"]

    assert entity.slots["status"].winner.event_id == keep.id
    assert entity.status == entities_mod.ACTIVE
    assert entity.stop_report is not None
    assert entity.stop_report.claim.event_id == stop.id
    assert "stop_reported_tier: patient-reported" in result.files[entity.rel_path].decode()


def test_an_unconfirmed_stop_proposal_is_not_a_reported_stop():
    """The annotation records a *user* act, not every reading the model offers.

    An untouched proposal is already in the queue as awaiting-confirmation;
    printing it on the page as well would state a change nobody has accepted.
    """
    stop = claim(DEVICE, "med:perindopril", "status", "stopped", ts=on_day(20),
                 tier="patient-reported")
    result = _med("2026-09-30T00:00:00Z", extra=[stop])
    entity = result.entities["med:perindopril"]

    assert entity.stop_report is None
    assert "stop_reported" not in result.files[entity.rel_path].decode()
    assert [item.kind for item in result.review] == ["awaiting-confirmation"]


def test_a_transitioned_stop_needs_no_annotation():
    """Once a prescriber-issued stop transitions, there is no discrepancy left."""
    result, _ = _reported_stop(tier="prescriber-issued")
    entity = result.entities["med:perindopril"]

    assert entity.status == entities_mod.STOPPED
    assert entity.stop_report is None
    assert "stop_reported" not in result.files[entity.rel_path].decode()
