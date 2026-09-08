"""Counting a supply from the literal spans on a script.

``MODELS.md``: "The model never does arithmetic or date math. It extracts literal
strings — ``30 tablets``, ``twice daily``, ``since around Easter``, ``1 repeat`` —
and Python parses them deterministically."

So these are the parsers, and the cases that matter most are the ones where they
say **no**. An unreadable span must produce no number at all, because the number
becomes an expected-exhaustion date, and a wrong one silently ages a medication
out of the record.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from agent.projection import dates, dispense


@pytest.mark.parametrize(
    "span,expected",
    [
        ("30 tablets", 30),
        ("30", 30),
        ("1 x 30 tablets", 30),
        ("2 x 30 tablets", 60),
        ("sixty capsules", 60),
        ("thirty tablets", 30),
        ("one pack of 30 tablets", 30),
        ("100ml", 100),
    ],
)
def test_quantities_that_can_be_read(span, expected):
    assert dispense.parse_quantity(span) == expected


@pytest.mark.parametrize(
    "span",
    [
        "a shoebox full",
        "as many as needed",
        "",
        "several",
        "enough for the trip",
        None,
    ],
)
def test_quantities_that_must_not_be_guessed(span):
    """"a shoebox full" must not become one. This is the fluent-wrong-answer case."""
    assert dispense.parse_quantity(span) is None


@pytest.mark.parametrize(
    "span,rate",
    [
        ("one daily", Fraction(1)),
        ("once daily", Fraction(1)),
        ("daily", Fraction(1)),
        ("mane", Fraction(1)),
        ("twice daily", Fraction(2)),
        ("bd", Fraction(2)),
        ("three times a day", Fraction(3)),
        ("tds", Fraction(3)),
        ("every other day", Fraction(1, 2)),
        ("weekly", Fraction(1, 7)),
    ],
)
def test_frequencies_and_their_rates(span, rate):
    from agent.projection import values

    assert values.canonical_frequency(span)[1] == rate


def test_as_needed_is_understood_but_has_no_rate():
    from agent.projection import values

    label, rate = values.canonical_frequency("as needed")
    assert label == "prn"
    assert rate is None


@pytest.mark.parametrize(
    "span,expected",
    [("no repeats", 0), ("nil repeats", 0), ("0", 0), ("1 repeat", 1), ("2 repeats", 2)],
)
def test_repeats(span, expected):
    assert dispense.parse_repeats(span) == expected


@pytest.mark.parametrize("span", ["a repeat", "some repeats", "", None])
def test_unreadable_repeats_are_not_guessed(span):
    assert dispense.parse_repeats(span) is None


@pytest.mark.parametrize(
    "block,days",
    [
        ({"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"}, 30),
        ({"quantity": "60 tablets", "frequency": "twice daily", "repeats": "2 repeats"}, 90),
        ({"quantity": "28 tablets", "frequency": "every other day", "repeats": "no repeats"}, 56),
        (
            {
                "quantity": "30 tablets",
                "frequency": "daily",
                "repeats": "no repeats",
                "dose_units": "two tablets",
            },
            15,
        ),
    ],
)
def test_days_of_supply(block, days):
    assert dispense.parse(block).days_supply == days


@pytest.mark.parametrize(
    "block",
    [
        {"quantity": "30 tablets", "frequency": "as needed", "repeats": "no repeats"},
        {"quantity": "30 tablets", "frequency": "when the pain is bad", "repeats": "0"},
        {"quantity": "30 tablets", "frequency": "one daily"},
        {"frequency": "one daily", "repeats": "no repeats"},
    ],
)
def test_an_uncountable_supply_says_why(block):
    supply = dispense.parse(block)
    assert supply.days_supply is None
    assert supply.unreadable, "an uncountable supply must explain itself"


def test_exhaustion_inherits_the_uncertainty_of_the_script_date():
    supply = dispense.parse(
        {"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"}
    )
    exact = dates.parse_occurred_at({"value": "2026-06-04"})
    assert supply.exhaustion(exact).iso == "2026-07-04"
    assert supply.exhaustion(exact).is_exact

    fuzzy = dates.parse_occurred_at(
        {"value": "2026-06-04", "precision": "month", "uncertainty_days": 14}
    )
    inherited = supply.exhaustion(fuzzy)
    assert inherited.precision == "month"
    assert inherited.uncertainty_days == 14


def test_no_start_date_means_no_exhaustion_date():
    supply = dispense.parse(
        {"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"}
    )
    assert supply.exhaustion(None) is None


def test_arithmetic_is_exact_rather_than_floating_point():
    supply = dispense.parse(
        {"quantity": "7 tablets", "frequency": "three times a day", "repeats": "no repeats"}
    )
    assert supply.days_supply == 2  # floored: a part-day of supply is not a day
