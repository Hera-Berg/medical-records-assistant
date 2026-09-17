"""Dose comparison keys: nothing silently dropped, nothing silently equated.

The key decides whether two readings agree. Before phase 11 the words after a
strength were dropped from it whenever they were not an exact schedule phrase,
so "5mg twice daily with food" and "5mg" compared as the same dose — and a dose
without its frequency is not the dose on the page.
"""

from __future__ import annotations

import pytest

from agent.projection import values


def key(text: str) -> str:
    parsed = values.parse(text)
    assert parsed is not None
    return parsed.key


@pytest.mark.parametrize(
    "literal",
    [
        "5mg daily",
        "5mg once daily",
        "5mg Take ONE tablet daily in the morning",
        "5mg one tablet daily",
        "5mg daily with food",
    ],
)
def test_one_schedule_written_many_ways_is_one_key(literal):
    assert key(literal) == "amount=5|frequency=1/day|unit=mg"


def test_a_frequency_is_never_dropped_to_make_two_readings_agree():
    assert key("5mg") != key("5mg twice daily")
    assert key("5mg twice daily in the morning") != key("5mg")
    assert "instruction=" in key("5mg twice daily in the morning")


def test_two_tablets_is_not_the_dose_one_tablet_is():
    assert key("5mg TWO tablets daily") != key("5mg ONE tablet daily")
    assert key("20mg Two at night") != key("20mg One at night")


def test_a_bare_count_before_a_schedule_is_a_count():
    assert key("20mg One at night") == key("20mg at night")


def test_a_spoken_amount_needs_a_unit_after_it():
    assert key("forty milligrams daily") == key("40mg daily")
    assert key("a tablet") == "a tablet"
    assert key("forty") == "forty"


def test_the_second_language_script_reads_as_the_same_schedule():
    assert key("5 mg Un comprimé par jour") == key("5mg daily")
    assert key("5 mg par jour") == key("5mg daily")


def test_the_literal_is_still_what_renders():
    parsed = values.parse("5mg Take ONE tablet daily in the morning")
    assert parsed.literal == "5mg Take ONE tablet daily in the morning"
