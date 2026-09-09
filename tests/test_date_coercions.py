"""Dates the payload states in a form the parser cannot use as written.

Date uncertainty is first-class: "a timeline that fakes precision is worse than
one that shows fuzz". So an unrecognised precision is widened rather than
believed — blurrier, never sharper — and that is the right behaviour. What it
must not be is silent.

The reason it cannot rely on phase 4 validating this away is the same one that
makes the consequence tier a lookup rather than a payload field: the log can hold
claims the current extractor never produced. Another device's shard, a hand edit,
a re-extraction under a future model version. A guarantee that only holds for
claims the current code wrote is not a guarantee about the record.
"""

from __future__ import annotations

import pytest

from agent import projection
from agent.projection import dates

from .conftest import claim, confirm, ingested, on_day

DEVICE = "laptop-a1b2"
AS_OF = "2026-09-30T00:00:00Z"


def _project(events):
    return projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)


def _with_occurred(occurred):
    proposed = claim(
        DEVICE, "med:sertraline", "dose", "50mg daily", ts=on_day(2), occurred=occurred
    )
    return _project(
        [ingested(DEVICE), proposed, confirm(DEVICE, proposed.id, ts=on_day(3))]
    )


def test_an_unrecognised_precision_still_widens():
    """The behaviour is unchanged; only the silence is fixed."""
    parsed = dates.parse_occurred_at(
        {"value": "2026-08-24", "precision": "week", "uncertainty_days": 4}
    )
    assert parsed.precision == "year"
    assert parsed.uncertainty_days == 4


def test_an_unrecognised_precision_is_reported_against_its_entity():
    result = _with_occurred(
        {"value": "2026-08-24", "precision": "week", "uncertainty_days": 4}
    )
    notes = [note for note in result.anomalies if "precision" in note]

    assert len(notes) == 1
    assert "'week' is not one of day, month, year" in notes[0]
    assert "widened to a whole year" in notes[0]
    assert notes[0].subject_id == "med:sertraline"

    page = result.files[result.entities["med:sertraline"].rel_path].decode("utf-8")
    assert "## Anomalies" in page
    assert "widened to a whole year" in page


def test_a_malformed_uncertainty_says_which_way_it_erred():
    """Dropping the slack makes the date sharper, which is the unsafe direction.

    There is no honest wider value to substitute — inventing a band would be
    worse than reporting — so the note names the direction rather than hiding it.
    """
    result = _with_occurred(
        {"value": "2026-08-01", "precision": "day", "uncertainty_days": "fourteen"}
    )
    notes = [note for note in result.anomalies if "uncertainty_days" in note]

    assert len(notes) == 1
    assert "sharper than the payload claimed" in notes[0]
    assert notes[0].subject_id == "med:sertraline"


@pytest.mark.parametrize(
    "occurred",
    [
        {"value": "2026-08-01"},
        {"value": "2026-08-01", "precision": "month", "uncertainty_days": 14},
        {"value": "2026-08-01", "precision": "year", "uncertainty_days": 0},
        "2026-08-01",
    ],
)
def test_a_well_formed_date_reports_nothing(occurred):
    assert _with_occurred(occurred).anomalies == ()


def test_a_derived_date_does_not_report_the_same_problem_twice():
    """Expected exhaustion is counted from the script date and inherits its fuzz.

    It must not inherit the note as well: one malformed payload is one anomaly,
    however many dates are computed from it.
    """
    proposed = claim(
        DEVICE, "med:sertraline", "dose", "50mg daily", ts=on_day(2),
        occurred={"value": "2026-08-01", "precision": "fortnight"},
        dispense={"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"},
    )
    result = _project(
        [ingested(DEVICE), proposed, confirm(DEVICE, proposed.id, ts=on_day(3))]
    )
    entity = result.entities["med:sertraline"]

    assert entity.expected_exhaustion is not None
    assert entity.expected_exhaustion.precision == "year"
    assert entity.expected_exhaustion.coercions == ()
    assert len(result.anomalies) == 1


def test_coercions_do_not_affect_whether_two_dates_are_equal():
    """A date is the instant it means, not the payload it was written in."""
    clean = dates.parse_occurred_at({"value": "2026-08-01", "precision": "year"})
    coerced = dates.parse_occurred_at({"value": "2026-08-01", "precision": "aeon"})

    assert coerced.coercions
    assert clean == coerced
    assert len({clean, coerced}) == 1
