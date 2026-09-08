"""Canonical serialisation must be stable; the byte-identical rebuild rests on it."""

from __future__ import annotations

import json

import pytest

from agent.events import canonical


def test_keys_are_sorted_and_whitespace_is_absent():
    assert canonical.dumps({"b": 1, "a": {"z": 2, "y": 3}}) == '{"a":{"y":3,"z":2},"b":1}'


def test_insertion_order_does_not_change_the_bytes():
    first = canonical.dumps({"a": 1, "b": 2, "c": {"x": 1, "y": 2}})
    second = canonical.dumps({"c": {"y": 2, "x": 1}, "b": 2, "a": 1})
    assert first == second


def test_non_ascii_is_written_literally():
    assert canonical.dumps({"name": "Dr Nguyễn"}) == '{"name":"Dr Nguyễn"}'


def test_round_trip_is_byte_identical():
    original = {
        "id": "01J8F2K3M4N5P6Q7R8S9T0ABCD",
        "payload": {"value": {"amount": 5, "unit": "mg"}, "confidence": 0.82},
        "provenance": None,
        "tags": ["a", "b"],
    }
    once = canonical.dumps(original)
    twice = canonical.dumps(canonical.loads(once))
    assert once == twice


def test_dump_line_is_utf8_and_newline_terminated():
    line = canonical.dump_line({"a": "é"})
    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    assert line.decode("utf-8") == '{"a":"é"}\n'


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_infinity_are_rejected_not_coerced(value):
    with pytest.raises(ValueError):
        canonical.dumps({"confidence": value})


def test_non_string_keys_are_rejected_rather_than_coerced():
    # json.dumps would silently turn 1 into "1"; a silent rewrite here is how a
    # dose becomes wrong somewhere downstream.
    with pytest.raises(TypeError):
        canonical.dumps({1: "x"})


def test_non_json_types_are_rejected():
    with pytest.raises(TypeError):
        canonical.dumps({"when": {1, 2}})


def test_floats_survive_a_round_trip_exactly():
    for value in (0.82, 0.1, 1e-7, 1234567.891):
        assert canonical.loads(canonical.dumps({"v": value}))["v"] == value


def test_loads_raises_on_malformed_input():
    with pytest.raises(json.JSONDecodeError):
        canonical.loads("{not json")
