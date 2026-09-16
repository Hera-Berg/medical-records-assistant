"""Phase 10 over HTTP: the route, and the one thing it must never do.

``POST /api/ask`` is a read that looks like a write — it is a POST, it runs
inference, and it is the only route that does either while somebody waits. So
the first half of this file is about proving it is a read: **asking changes
nothing**, not the event log, not the wiki, not one byte anywhere in the vault,
and not even a record of having been asked.

The second half is the states the screen has to render, each produced through
the real route rather than posed: an answer with citations, an empty retrieval,
a refused question, a sleeping box, and a question aimed at rejected content.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.errors import AuthRejected, EndpointUnreachable
from agent.query import prompts

from .conftest import FakeBox, claim, confirm, ingested, on_day, reject


@pytest.fixture
def seeded(vault):
    """A vault with a little of everything a question might touch."""
    DEVICE = vault.identity.id
    events = [ingested(DEVICE, "a3f91c", ts=on_day(2))]
    dose = claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(3),
                 artifact="a3f91c", occurred={"value": "2026-09-02", "precision": "day"})
    events += [dose, confirm(DEVICE, dose.id, ts=on_day(3, hour=12))]

    misheard = claim(DEVICE, "problem:alcohol-dependence", "name", "alcohol dependence",
                     ts=on_day(4), artifact="a3f91c", tier="patient-reported",
                     subject_name="alcohol dependence")
    events += [misheard, reject(DEVICE, misheard.id, ts=on_day(5))]

    for event in sorted(events, key=lambda e: e.sort_key):
        vault.append(event)
    return vault


@pytest.fixture
def asking(seeded, monkeypatch):
    """A client whose box is a :class:`FakeBox` the test can configure."""
    from agent.extract import session

    from .conftest import api_client

    box = FakeBox(
        sentences=[{"text": "You take perindopril 5mg daily.", "source": "a3f91c"}]
    )
    monkeypatch.setattr(session, "open_client", lambda *a, **k: box)
    with api_client(seeded) as client:
        client.box = box
        yield client


def fingerprint(root: Path) -> dict[str, str]:
    """Every file in the vault, by content. Nothing may move."""
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            found[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


# -- queries are not events --------------------------------------------------


def test_asking_appends_nothing_and_writes_nothing(asking, seeded):
    before = fingerprint(seeded.root)

    for question in (
        "what dose of perindopril am I on",
        "what is the capital of France",
        "should I be worried about the dose",
        "what allergies do I have",
    ):
        assert asking.post("/api/ask", json={"question": question}).status_code == 200

    assert fingerprint(seeded.root) == before


def test_asking_leaves_no_record_of_having_been_asked(asking, seeded):
    """Deliberately no local access log. See the package docstring.

    A file of the patient's own questions would be the most disclosing thing in
    a folder that syncs to Dropbox, and questions disclose more than answers do.
    """
    asking.post("/api/ask", json={"question": "what dose of perindopril am I on"})

    names = [path.name for path in seeded.root.rglob("*") if path.is_file()]
    assert not any("quer" in name or "ask" in name for name in names)


def test_asking_never_changes_the_event_count(asking):
    before = asking.get("/api/health").json()["record"]["events"]

    asking.post("/api/ask", json={"question": "what am I taking"})

    assert asking.get("/api/health").json()["record"]["events"] == before


def test_asking_cannot_create_or_modify_a_claim(asking):
    before = asking.get("/api/wiki").json()

    asking.post("/api/ask", json={"question": "what dose of perindopril am I on"})

    assert asking.get("/api/wiki").json() == before


# -- the states the screen renders -------------------------------------------


def test_a_good_answer_carries_a_citation_on_every_sentence(asking):
    body = asking.post(
        "/api/ask", json={"question": "what dose of perindopril am I on"}
    ).json()

    assert body["state"] == "answered"
    assert body["sentences"]
    for sentence in body["sentences"]:
        assert sentence["text"]
        assert sentence["citation"]["resolved"]
        assert sentence["citation"]["url"]


def test_an_empty_retrieval_says_so_and_never_asks_the_model(asking):
    body = asking.post(
        "/api/ask", json={"question": "what is the capital of France"}
    ).json()

    assert body["state"] == "empty"
    assert body["message"] == "Nothing in your record covers that."
    assert body["sentences"] == []
    assert asking.box.answering_calls == []


def test_a_refused_question_is_refused_before_anything_is_read(asking):
    body = asking.post(
        "/api/ask", json={"question": "should I be worried about this dose"}
    ).json()

    assert body["state"] == "refused"
    assert body["refusal"] == "seriousness"
    assert body["found"] == []
    assert "appointment" in body["refusal_next"]
    assert asking.box.calls == []


def test_a_sleeping_box_still_answers_from_the_record(asking):
    asking.box.error = EndpointUnreachable("the box is asleep")

    body = asking.post(
        "/api/ask", json={"question": "what dose of perindopril am I on"}
    ).json()

    assert body["state"] == "offline"
    assert body["box"] == "unreachable"
    assert body["found"], "retrieval runs without the box and is most of the value"
    assert all(entry["citation"]["resolved"] for entry in body["found"])


def test_a_rejected_key_reads_differently_from_a_sleeping_box(asking):
    asking.box.error = AuthRejected("401")

    body = asking.post(
        "/api/ask", json={"question": "what dose of perindopril am I on"}
    ).json()

    assert body["box"] == "unauthorised"
    assert "password" in body["message"]


def test_a_failed_question_updates_what_health_reports_about_the_box(asking):
    asking.box.error = AuthRejected("401")
    asking.post("/api/ask", json={"question": "what dose of perindopril am I on"})

    assert asking.get("/api/health").json()["endpoint"]["state"] == "unauthorised"


def test_a_successful_question_does_not_claim_the_box_can_read_pictures(asking):
    """Answering text proves nothing about vision. See the route.

    "Working" in this application means the startup probe passed, vision
    included. A box that answers questions perfectly and discards every
    photograph is the failure `probe_vision` exists to catch, and this route
    must not paper over it.
    """
    asking.post("/api/ask", json={"question": "what dose of perindopril am I on"})

    assert asking.get("/api/health").json()["endpoint"]["state"] != "working"


def test_a_question_aimed_at_rejected_content_returns_none_of_it(asking):
    for question in (
        "do I have a problem with alcohol",
        "what does my record say about alcohol dependence",
        "list my problems",
    ):
        body = asking.post("/api/ask", json={"question": question}).json()
        # The question itself is echoed, because the screen prints it above the
        # answer. Those are the user's own words, this second. Everything else
        # in the response is the record talking, and none of it may carry this.
        answered = {key: value for key, value in body.items() if key != "question"}
        assert "alcohol" not in repr(answered).lower(), question

    assert "alcohol" not in asking.box.record_sent().lower()


# -- follow-ups --------------------------------------------------------------


def test_a_follow_up_inherits_the_subject_and_still_costs_one_generation(asking):
    first = asking.post(
        "/api/ask", json={"question": "what dose of perindopril am I on"}
    ).json()
    second = asking.post(
        "/api/ask",
        json={
            "question": "when was that",
            "history": [
                {
                    "question": "what dose of perindopril am I on",
                    "answer": " ".join(s["text"] for s in first["sentences"]),
                }
            ],
        },
    ).json()

    assert second["state"] == "answered"
    assert len(asking.box.answering_calls) == 2, "one generation per question"
    assert "Perindopril" in asking.box.answering_calls[1]["text"]


def test_the_conversation_is_capped_and_the_screen_is_told_how_much_is_left(asking):
    body = asking.post("/api/ask", json={"question": "what am I taking"}).json()
    assert body["turns_left"] == 2

    body = asking.post(
        "/api/ask",
        json={
            "question": "and when",
            "history": [
                {"question": "one", "answer": "a"},
                {"question": "two", "answer": "b"},
            ],
        },
    ).json()
    assert body["turns_left"] == 0


def test_more_history_than_the_cap_is_trimmed_not_refused(asking):
    body = asking.post(
        "/api/ask",
        json={
            "question": "what am I taking",
            "history": [{"question": f"q{n}", "answer": "a"} for n in range(20)],
        },
    ).json()

    assert body["state"] == "answered"


# -- the shape of the request ------------------------------------------------


def test_an_empty_question_is_answered_rather_than_erroring(asking):
    body = asking.post("/api/ask", json={"question": "  "}).json()

    assert body["state"] == "no-question"
    assert asking.box.calls == []


def test_something_pasted_instead_of_asked_is_refused_with_a_sentence(asking):
    body = asking.post("/api/ask", json={"question": "x" * 5000}).json()

    assert body["state"] == "too-long"
    assert "too long to be a question" in body["message"]
    assert asking.box.calls == []


def test_a_body_that_is_not_a_question_at_all_does_not_crash(asking):
    for body in ({}, {"question": None}, {"question": 12}, {"history": "nope"}):
        assert asking.post("/api/ask", json=body).status_code == 200


def test_the_prompt_stays_under_the_cap(asking):
    asking.post("/api/ask", json={"question": "what am I taking"})

    sent = asking.box.answering_calls[0]["text"]
    assert len(sent) < prompts.MAX_SENTENCES * 100_000  # sanity, not the rule
    from agent.query import context as context_mod

    assert len(sent) < context_mod.MAX_PROMPT_CHARS
