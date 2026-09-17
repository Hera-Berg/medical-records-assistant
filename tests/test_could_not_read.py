"""What a reader could not read reaches a person — or the reader has not failed safely.

The eval's bar is that nothing about medications, doses or allergies is wrong,
and everything not read correctly is an abstention. That bar only means
something if an abstention is visible. These tests are the other half of it:
every reading that was unreadable or declined part of a page raises a
high-consequence item in the review queue, names what could not be read and
never a value, and leaves the queue only when a person says they dealt with it
or a newer reading replaces it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent.events import envelope
from agent.projection import project, reading as reading_mod, unread

from .conftest import api_client, ingested

DEVICE = "test-device"
AS_OF = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _read(short="a3f91c", ts="2026-09-10T10:00:00Z", readable=True, abstentions=(), reason=None, truncated=False, device=DEVICE):
    return envelope.new(
        "extraction.completed",
        device,
        ts=ts,
        provenance={"model": "qwen3.5-4b-q4_k_m@00fe7986ff5f", "artifact": short, "runtime_kind": "bundled"},
        payload={
            "artifact": short,
            "readable": readable,
            "unreadable_reason": reason,
            "truncated": truncated,
            "abstentions": list(abstentions),
            "turns": [],
        },
    )


METFORMIN_FREQUENCY = {
    "family": "medications", "field": "frequency", "reason": "handwriting",
    "subject": "med:metformin", "subject_name": "Metformin", "source_span": "Metformin 500mg tw…",
}
NAMELESS = {
    "family": "medications", "field": "whole entry", "reason": "blurred",
    "subject": None, "subject_name": None, "source_span": None,
}


def _items(events):
    return [item for item in project(events, AS_OF).review if item.kind == unread.COULD_NOT_READ]


def test_an_abstention_is_a_high_item_naming_what_was_not_read_and_no_value():
    events = [ingested(DEVICE), _read(abstentions=[METFORMIN_FREQUENCY, NAMELESS])]
    (item,) = _items(events)

    assert item.consequence == "high"
    assert item.cite == "a3f91c"
    assert "Metformin: how often it is taken" in item.summary
    assert "a medication it could not read at all" in item.summary
    assert "500mg" not in item.summary, "an abstention has no value to repeat"


def test_an_unreadable_page_is_an_item_too():
    events = [ingested(DEVICE), _read(readable=False, reason="the photograph is out of focus")]
    (item,) = _items(events)
    assert "could not be read (the photograph is out of focus)" in item.summary


def test_a_cut_off_reading_is_an_item_that_says_why():
    events = [ingested(DEVICE), _read(readable=False, truncated=True, reason="…")]
    (item,) = _items(events)
    assert "ran out of room" in item.summary


def test_a_clean_reading_raises_nothing():
    assert _items([ingested(DEVICE), _read()]) == []


def test_dealing_with_it_takes_it_off_the_list_and_finishes_the_document():
    read = _read(abstentions=[METFORMIN_FREQUENCY])
    events = [ingested(DEVICE), read]
    states = reading_mod.index(events, {"a3f91c": object()})
    assert not states["a3f91c"].is_finished
    assert "could not be read" in states["a3f91c"].sentence()

    done = envelope.new("reading.acknowledged", DEVICE, ts="2026-09-11T10:00:00Z",
                        payload={"target": read.id, "artifact": "a3f91c"})
    events.append(done)
    assert _items(events) == []
    assert reading_mod.index(events, {"a3f91c": object()})["a3f91c"].is_finished


def test_an_unreadable_page_is_not_finished_until_dealt_with():
    read = _read(readable=False, reason="blurred")
    states = reading_mod.index([ingested(DEVICE), read], {"a3f91c": object()})
    assert not states["a3f91c"].is_finished


def test_a_newer_reading_replaces_the_question():
    old = _read(abstentions=[METFORMIN_FREQUENCY])
    better = _read(ts="2026-09-12T10:00:00Z")
    assert _items([ingested(DEVICE), old, better]) == []


def test_a_newer_reading_that_also_cannot_read_asks_again_after_an_acknowledgement():
    old = _read(abstentions=[METFORMIN_FREQUENCY])
    done = envelope.new("reading.acknowledged", DEVICE, ts="2026-09-11T10:00:00Z",
                        payload={"target": old.id, "artifact": "a3f91c"})
    again = _read(ts="2026-09-12T10:00:00Z", abstentions=[NAMELESS])
    (item,) = _items([ingested(DEVICE), old, done, again])
    assert item.targets == (again.id,)


def test_a_voice_note_transcript_is_not_mistaken_for_a_reading():
    transcript = envelope.new(
        "extraction.completed", DEVICE, ts="2026-09-10T10:00:00Z",
        provenance={"model": "small", "artifact": "a3f91c"},
        payload={"artifact": "a3f91c", "reader": "speech", "transcript": "", "readable": False},
    )
    assert _items([ingested(DEVICE, mime="audio/webm"), transcript]) == []


def test_the_inbox_offers_one_act_and_records_it(vault):
    identity = vault.identity.id
    read = _read(abstentions=[METFORMIN_FREQUENCY], device=identity)
    for event in (ingested(identity), read):
        vault.append(event)

    with api_client(vault) as client:
        (item,) = [i for i in client.get("/api/review").json()["tiers"]["high"] if i["kind"] == "could-not-read"]
        assert item["actions"] == ["dealt-with"]
        assert item["unread"] == {"artifact": "a3f91c"}
        assert client.post(f"/api/review/{item['id']}", json={"action": "confirm"}).status_code == 400

        answered = client.post(f"/api/review/{item['id']}", json={"action": "dealt-with"})
        assert answered.status_code == 200
        again = client.post(f"/api/review/{item['id']}", json={"action": "dealt-with"})
        assert again.status_code == 409
        assert "marked as dealt with" in again.json()["decided"]["message"]

    acknowledgements = [e for e in vault.read().events if e.type == "reading.acknowledged"]
    assert [e.payload["target"] for e in acknowledgements] == [read.id]
    assert acknowledgements[0].actor == "user"


def test_an_unread_document_is_named_in_words_and_counted_apart(vault):
    identity = vault.identity.id
    for event in (ingested(identity), _read(abstentions=[METFORMIN_FREQUENCY], device=identity)):
        vault.append(event)

    with api_client(vault) as client:
        (item,) = client.get("/api/review").json()["tiers"]["high"]
        health = client.get("/api/health").json()

    assert item["name"] == "A document read only in part"
    assert "artifact:" not in item["name"]
    assert health["review"]["could_not_read"] == 1
    assert health["review"]["by_tier"]["high"] == 0, "not something to confirm"
