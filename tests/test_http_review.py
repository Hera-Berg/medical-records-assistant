"""Acting on the queue: the one route that lets a claim into the record.

Everything here is about making that deliberate rather than easy. The route
appends user events and the projection reduces them, so what is tested is which
events one tap produces, which taps are refused outright, and what a user is told
when the answer is no.

The two rules that need a route to test them at all:

**One review item per fact, not per source.** Two documents stating the same dose
are one decision, and confirming it emits one ``claim.confirmed`` per claim — the
queue must not come back and ask about the second document.

**A stop proposal is its own action.** ``confirm`` against one is refused with a
sentence naming the action that does stop a medication. That refusal is the
guard; the frontend rendering a different button is the second copy of it.
"""

from __future__ import annotations

import json

import pytest

from agent.projection import reconcile

from .conftest import claim, confirm, ingested, on_day, reject

DEVICE_ARTIFACT = "a3f91c"


def _events(vault, *events):
    for event in sorted(events, key=lambda e: e.sort_key):
        vault.append(event)
    return vault


def _post(client, item_id, **body):
    return client.post(f"/api/review/{item_id}", json=body)


def _items(client, tier="high"):
    return client.get("/api/review").json()["tiers"][tier]


def _find(client, subject_id):
    body = client.get("/api/review").json()
    for tier in ("high", "medium", "low"):
        for item in body["tiers"][tier]:
            if item["subject_id"] == subject_id:
                return item
    return None


# --- folding ---------------------------------------------------------------


@pytest.fixture
def two_sources(vault):
    """Two scripts stating the same dose, neither confirmed."""
    device = vault.identity.id
    first = claim(
        device, "med:perindopril", "dose", "5mg daily", ts=on_day(3), artifact="a3f91c"
    )
    second = claim(
        device, "med:perindopril", "dose", "5mg daily", ts=on_day(4), artifact="77b210"
    )
    return _events(
        vault,
        ingested(device, "a3f91c", ts=on_day(1)),
        ingested(device, "77b210", ts=on_day(2)),
        first,
        second,
    )


def test_one_tap_decides_every_source_that_says_the_same_thing(two_sources, client):
    """One fact, one item, one tap — and one confirmation per claim.

    A queue that asked twice about one fact is what makes an inbox
    uncompletable, and the wiki already cites several sources for one value.
    """
    item = _find(client, "med:perindopril")
    assert item["sources_folded"] == 2
    assert len(item["citations"]) == 2

    body = _post(client, item["id"], action="confirm").json()
    assert body["decided"]["claims_decided"] == 2

    # And it does not come back to ask about the second document.
    assert _find(client, "med:perindopril") is None
    detail = client.get("/api/wiki/med:perindopril").json()
    assert detail["dose"] == "5mg daily"
    assert sorted(detail["sources"]) == ["77b210", "a3f91c"]


def test_deciding_by_any_of_the_folded_ids_finds_the_same_item(two_sources, client):
    """Two devices can hold different ids for one fold; both must work."""
    item = _find(client, "med:perindopril")
    other = [t for t in item["targets"] if t != item["id"]][0]

    body = _post(client, other, action="confirm").json()
    assert body["decided"]["claims_decided"] == 2


def test_different_values_for_one_slot_stay_separate(vault, client):
    """That is a conflict, not a duplicate, and it is not one tap."""
    device = vault.identity.id
    _events(
        vault,
        ingested(device, "a3f91c", ts=on_day(1)),
        ingested(device, "77b210", ts=on_day(2)),
        claim(device, "med:perindopril", "dose", "5mg daily", ts=on_day(3), artifact="a3f91c"),
        claim(device, "med:perindopril", "dose", "10mg daily", ts=on_day(4), artifact="77b210"),
    )
    items = [i for i in _items(client) if i["subject_id"] == "med:perindopril"]
    assert len(items) == 2
    assert {i["proposed"]["value"]["literal"] for i in items} == {"5mg daily", "10mg daily"}


# --- stopping a medication -------------------------------------------------


@pytest.fixture
def a_stop(vault):
    """A confirmed medication, and a prescriber-issued document saying to cease."""
    device = vault.identity.id
    dose = claim(device, "med:perindopril", "dose", "5mg daily", ts=on_day(2))
    stop = claim(
        device, "med:perindopril", "status", "stopped", ts=on_day(4),
        tier="prescriber-issued",
    )
    return _events(
        vault, ingested(device, "a3f91c", ts=on_day(1)), dose,
        confirm(device, dose.id, ts=on_day(2, hour=11)), stop,
    )


def test_a_generic_confirm_cannot_stop_a_medication(a_stop, client):
    """The distinct action is enforced by the route, not only drawn by the UI.

    A run of taps down a queue must not be able to drop a medication, so the
    act that does it has to be asked for by name.
    """
    item = _find(client, "med:perindopril")
    assert item["kind"] == reconcile.STOP_PROPOSED
    assert "confirm" not in item["actions"]

    response = _post(client, item["id"], action="confirm")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "confirm-stop" in detail
    assert "cannot drop a medication by accident" in detail or "drop a medication" in detail

    # Nothing happened. The medication is untouched and the item still waits.
    assert client.get("/api/wiki/med:perindopril").json()["status"] == "active"
    assert _find(client, "med:perindopril") is not None


def test_the_distinct_action_stops_it(a_stop, client):
    item = _find(client, "med:perindopril")
    assert item["stop"] == {"transitions": True, "evidence_tier": "prescriber-issued"}

    body = _post(client, item["id"], action="confirm-stop").json()
    assert "taken off your medication list" in body["decided"]["message"]
    assert client.get("/api/wiki/med:perindopril").json()["status"] == "stopped"


def test_a_patient_reported_stop_says_so_before_and_after_the_tap(vault, client):
    """The button's promise and the record's behaviour have to agree.

    Below prescriber tier a stop annotates rather than transitions. The queue
    says that before the tap and the answer repeats it after, because a user who
    tapped "it stays on the list" and then found it gone — or tapped expecting it
    gone and was not told otherwise — has been misled either way.
    """
    device = vault.identity.id
    dose = claim(device, "med:sertraline", "dose", "50mg daily", ts=on_day(2))
    stop = claim(
        device, "med:sertraline", "status", "stopped", ts=on_day(4),
        tier="patient-reported",
    )
    _events(
        vault, ingested(device, "a3f91c", ts=on_day(1)), dose,
        confirm(device, dose.id, ts=on_day(2, hour=11)), stop,
    )

    item = _find(client, "med:sertraline")
    assert item["stop"]["transitions"] is False
    assert "keeps it on your medication list" in item["summary"]

    body = _post(client, item["id"], action="confirm-stop").json()
    assert "stays on your medication list" in body["decided"]["message"]

    detail = client.get("/api/wiki/med:sertraline").json()
    assert detail["status"] == "active"
    assert detail["stop_report"]["tier"] == "patient-reported"


# --- correcting and rejecting ----------------------------------------------


def test_a_correction_replaces_the_reading_and_keeps_it_visible(two_sources, client):
    item = _find(client, "med:perindopril")
    _post(client, item["id"], action="correct", value="50mg daily")

    detail = client.get("/api/wiki/med:perindopril").json()
    dose = next(s for s in detail["slots"] if s["predicate"] == "dose")
    assert dose["winner"]["value"]["literal"] == "50mg daily"
    assert dose["winner"]["is_correction"] is True
    # What the correction acted on is still there. A mistyped correction is
    # undetectable once the original is gone.
    assert "5mg daily" in [c["value"]["literal"] for c in dose["superseded"]]


def test_an_empty_correction_is_refused_rather_than_recorded(two_sources, client):
    item = _find(client, "med:perindopril")
    response = _post(client, item["id"], action="correct", value="   ")

    assert response.status_code == 400
    assert "what the value should be" in response.json()["detail"]
    assert _find(client, "med:perindopril") is not None


def test_a_rejected_item_leaves_the_queue_and_says_nothing(two_sources, client):
    item = _find(client, "med:perindopril")
    _post(client, item["id"], action="reject")

    body = client.get("/api/review").json()
    assert _find(client, "med:perindopril") is None
    assert "5mg daily" not in json.dumps(body)
    assert client.get("/api/wiki/med:perindopril").status_code == 404


def test_a_rejection_survives_the_same_reading_being_proposed_again(two_sources, client, vault):
    """Re-extraction must not resurrect what the user retracted."""
    item = _find(client, "med:perindopril")
    _post(client, item["id"], action="reject")

    device = vault.identity.id
    vault.append(
        claim(device, "med:perindopril", "dose", "5.0mg daily", ts=on_day(9), artifact="a3f91c")
    )
    assert _find(client, "med:perindopril") is None


# --- a decision somebody else already made ---------------------------------


def test_deciding_something_already_decided_explains_and_refreshes(two_sources, client):
    """Two devices, or one tab left open. An ordinary Tuesday, not a fault.

    The answer says what happened to the thing they tapped and carries the
    current queue with it, so the user is looking at the truth rather than at a
    failed request they have to interpret.
    """
    item = _find(client, "med:perindopril")
    _post(client, item["id"], action="confirm")

    again = _post(client, item["id"], action="confirm")
    assert again.status_code == 409
    body = again.json()

    assert "already confirmed" in body["decided"]["message"]
    assert "Nothing was changed just now" in body["decided"]["message"]
    # The whole queue comes back with it, already correct.
    assert body["actionable"] is True
    assert body["counts"]["total"] == 0
    assert body["tiers"] == {"high": [], "medium": [], "low": []}


def test_an_unknown_id_is_explained_the_same_way(vault, client):
    device = vault.identity.id
    _events(vault, ingested(device, "a3f91c", ts=on_day(1)))

    response = _post(client, "01JQQQQQQQQQQQQQQQQQQQQQQQ", action="confirm")
    assert response.status_code == 409
    assert "no longer waiting for a decision" in response.json()["decided"]["message"]


# --- what the inbox reports about the record itself ------------------------


def test_the_inbox_reports_the_anomaly_count(vault, client):
    """CLAUDE.md puts anomalies here so they cannot scroll past unseen."""
    device = vault.identity.id
    _events(vault, ingested(device, "a3f91c", ts=on_day(1)), confirm(device, "", ts=on_day(2)))

    body = client.get("/api/review").json()
    assert body["anomalies"]["count"] >= 1
    assert body["anomalies"]["items"]


def _withdrawn(vault, subject: str, text: str):
    """One reading confirmed, the same reading re-extracted and then rejected.

    This is the shape that raises the ambiguity: two user decisions about the
    same reading of the same artefact, pointing opposite ways. A single claim
    confirmed and later rejected is not this — it is one person changing their
    mind, where the later act simply governs.
    """
    device = vault.identity.id
    first = claim(device, subject, "diagnosis", text, ts=on_day(3), artifact="a3f91c")
    again = claim(device, subject, "diagnosis", text, ts=on_day(6), artifact="a3f91c")
    return _events(
        vault,
        ingested(device, "a3f91c", ts=on_day(1)),
        first,
        confirm(device, first.id, ts=on_day(4)),
        again,
        reject(device, again.id, ts=on_day(7)),
    )


def test_a_withdrawal_carries_no_claims_and_can_be_settled_either_way(vault, client):
    """Confirmed, then rejected. The content is out; the ambiguity is raised.

    The item names its artefact and nothing else — printing what was withdrawn
    would re-assert exactly what the user said is not true of them — and both
    ways of settling it are one tap.
    """
    _withdrawn(vault, "problem:alcohol-dependence", "alcohol dependence")

    item = _find(client, "problem:alcohol-dependence")
    assert item["kind"] == reconcile.WITHDRAWN
    assert item["proposed"] is None
    assert "alcohol dependence" not in json.dumps(client.get("/api/review").json())
    assert sorted(item["actions"]) == ["confirm-again", "keep-rejected"]

    _post(client, item["id"], action="keep-rejected")
    assert _find(client, "problem:alcohol-dependence") is None
    assert "alcohol dependence" not in json.dumps(client.get("/api/review").json())


def test_a_withdrawal_can_be_confirmed_again(vault, client):
    _withdrawn(vault, "problem:migraine", "migraine")

    item = _find(client, "problem:migraine")
    _post(client, item["id"], action="confirm-again")

    assert _find(client, "problem:migraine") is None
    assert client.get("/api/wiki/problem:migraine").status_code == 200
