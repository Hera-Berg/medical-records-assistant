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
    assert b"expected_exhaustion: null" in result.files[entity.rel_path]


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
