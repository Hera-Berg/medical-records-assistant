"""ULIDs must sort in time order and never collide; the log's total order rests on it."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.events import ulid


def test_shape_is_26_crockford_characters():
    value = ulid.new()
    assert len(value) == ulid.ULID_LENGTH == 26
    assert ulid.is_valid(value)
    assert not set(value) & set("ILOU")


def test_lexical_order_matches_time_order():
    early = ulid.from_timestamp(1_000_000_000_000)
    late = ulid.from_timestamp(2_000_000_000_000)
    assert early < late


def test_monotonic_within_a_millisecond(monkeypatch):
    monkeypatch.setattr(ulid.time, "time", lambda: 1_700_000_000.0)
    same_ms = [ulid.new() for _ in range(500)]
    assert same_ms == sorted(same_ms)
    assert len(set(same_ms)) == len(same_ms)
    assert len({ulid.timestamp_ms(v) for v in same_ms}) == 1


def test_never_goes_backwards_when_the_clock_does(monkeypatch):
    clock = {"now": 1_700_000_000.0}
    monkeypatch.setattr(ulid.time, "time", lambda: clock["now"])
    forward = ulid.new()
    clock["now"] = 1_600_000_000.0  # NTP step backwards
    backward = ulid.new()
    assert backward > forward


def test_from_timestamp_does_not_disturb_the_live_clock():
    before = ulid.new()
    ulid.from_timestamp(1_000_000_000_000)
    assert ulid.new() > before


def test_unique_at_scale():
    values = [ulid.new() for _ in range(50_000)]
    assert len(set(values)) == len(values)
    assert values == sorted(values)


def test_embedded_timestamp_round_trips():
    ms = 1_757_000_000_123
    value = ulid.from_timestamp(ms)
    assert ulid.timestamp_ms(value) == ms
    assert ulid.timestamp(value) == datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "01J8F2K3M4N5P6Q7R8S9T0",  # the 22-char example in CLAUDE.md is not a ULID
        "01J8F2K3M4N5P6Q7R8S9T0ABCD" .replace("A", "I"),  # excluded letter
        "Z" * 26,  # overflows 128 bits
        12345,
    ],
)
def test_rejects_non_ulids(bad):
    assert not ulid.is_valid(bad)
