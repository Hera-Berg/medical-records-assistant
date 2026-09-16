"""Phase 10: every sentence carries a citation, or it is not shown at all.

This is the file that holds the feature's central promise. CLAUDE.md: "Every
sentence carries a citation to an artefact or event. A sentence that cannot be
attributed is dropped before rendering, not shipped with a hedge."

Dropped, specifically — not greyed out, not flagged, not prefixed with "the
record does not say this, but". A hedge on a medical record reads as a fact with
a caveat, and the caveat is the first thing lost when a page is skimmed,
photographed or read aloud in a consulting room.

The other half is what happens when there is nothing to cite. An empty retrieval
returns "nothing in your record covers that" and **the answering model is never
called** — tested below with a question the model certainly knows the answer to
and the record certainly does not contain.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent import query
from agent.errors import AuthRejected, EndpointUnreachable
from agent.query import prompts, render

from .conftest import FakeBox, claim, confirm, ingested, on_day

DEVICE = "laptop-a1b2"
AS_OF = datetime(2026, 9, 16, tzinfo=timezone.utc)


def a_record():
    events = [ingested(DEVICE, "a3f91c", ts=on_day(2))]
    dose = claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(3),
                 artifact="a3f91c", occurred={"value": "2026-09-02", "precision": "day"})
    events += [dose, confirm(DEVICE, dose.id, ts=on_day(3, hour=12))]
    return query.Record.of(sorted(events, key=lambda e: e.sort_key), AS_OF)


def ask(question, box=None, **kwargs):
    return query.ask(question, a_record(), client=box, **kwargs)


# -- the citation rule -------------------------------------------------------


def test_a_sentence_citing_a_retrieved_source_is_kept():
    box = FakeBox(sentences=[{"text": "You take perindopril 5mg daily.", "source": "a3f91c"}])

    answer = ask("what dose of perindopril am I on", box)

    assert answer.state == render.ANSWERED
    assert len(answer.sentences) == 1
    assert answer.sentences[0].citation.resolved
    assert answer.sentences[0].citation.target


def test_a_sentence_citing_something_it_was_not_shown_is_dropped():
    box = FakeBox(
        sentences=[
            {"text": "You take perindopril 5mg daily.", "source": "a3f91c"},
            {"text": "Perindopril is an ACE inhibitor.", "source": "ffffff"},
        ]
    )

    answer = ask("what dose of perindopril am I on", box)

    assert [sentence.text for sentence in answer.sentences] == [
        "You take perindopril 5mg daily."
    ]
    assert answer.dropped == 1


def test_a_dropped_sentence_is_gone_and_not_hedged():
    """Nothing of the dropped sentence survives anywhere in the answer."""
    box = FakeBox(
        sentences=[
            {"text": "You take perindopril 5mg daily.", "source": "a3f91c"},
            {"text": "This is probably fine.", "source": "not-a-key"},
        ]
    )

    answer = ask("what dose of perindopril am I on", box)

    assert "probably fine" not in answer.text
    assert "probably fine" not in answer.message


def test_a_sentence_with_no_source_at_all_is_dropped():
    box = FakeBox(sentences=[{"text": "You take perindopril.", "source": ""}])

    answer = ask("what dose of perindopril am I on", box)

    assert answer.state == render.UNATTRIBUTED
    assert answer.sentences == ()


def test_every_sentence_being_dropped_is_its_own_state():
    """Not the same as "your record does not cover that".

    The record may cover the question perfectly well; what failed is the
    answer. Showing the empty-retrieval sentence here would tell somebody their
    record is silent about something it is not silent about.
    """
    box = FakeBox(sentences=[{"text": "Something.", "source": "nope"}])

    answer = ask("what dose of perindopril am I on", box)

    assert answer.state == render.UNATTRIBUTED
    assert answer.message != render.MESSAGES[render.EMPTY]
    # The entries that matched are still offered, each one cited.
    assert answer.retrieval.passages
    assert answer.sources


# -- empty retrieval ---------------------------------------------------------


def test_empty_retrieval_never_reaches_the_answering_model():
    """The question is one the model certainly knows and the record does not hold.

    The failure this prevents is the whole reason the rule exists: a fluent,
    confident, entirely ungrounded answer that looks exactly like the grounded
    ones beside it.
    """
    box = FakeBox(sentences=[{"text": "Paris.", "source": "a3f91c"}])

    answer = ask("what is the capital of France", box)

    assert answer.state == render.EMPTY
    assert answer.message == "Nothing in your record covers that."
    assert answer.sentences == ()
    assert box.answering_calls == []


def test_empty_retrieval_answers_the_same_with_no_box_at_all():
    answer = ask("what is the capital of France", box=None)

    assert answer.state == render.EMPTY


def test_a_medical_question_the_record_does_not_hold_is_also_empty():
    box = FakeBox(sentences=[{"text": "Anything.", "source": "a3f91c"}])

    answer = ask("when did I have my gallbladder removed", box)

    assert answer.state == render.EMPTY
    assert box.answering_calls == []


# -- the model's failures, kept apart ----------------------------------------


def test_running_out_of_room_is_not_reported_as_a_bad_answer():
    """``finish_reason: length`` is its own failure, per MODELS.md.

    They arrive at the parser looking identical and they want opposite
    investigations, so the state and the sentence are different.
    """
    box = FakeBox(sentences=[{"text": "You take", "source": "a3f91c"}], truncated=True)

    answer = ask("what dose of perindopril am I on", box)

    assert answer.state == render.CUT_OFF
    assert "stopped before it was finished" in answer.message
    assert answer.state != render.UNREADABLE


def test_an_unreadable_answer_is_reported_and_never_repaired():
    box = FakeBox(content="Sure! Here is what I found: you take perindopril.")

    answer = ask("what dose of perindopril am I on", box)

    assert answer.state == render.UNREADABLE
    assert answer.sentences == ()
    # Not scraped out of the prose. Validate, never repair.
    assert "perindopril" not in answer.text


def test_an_unreachable_box_still_answers_from_the_record():
    box = FakeBox(error=EndpointUnreachable("asleep"))

    answer = ask("what dose of perindopril am I on", box)

    assert answer.state == render.OFFLINE
    assert answer.box == "unreachable"
    assert answer.retrieval.passages
    assert answer.sources


def test_a_rejected_key_is_not_reported_as_a_sleeping_box():
    box = FakeBox(error=AuthRejected("nope"))

    answer = ask("what dose of perindopril am I on", box)

    assert answer.box == "unauthorised"
    assert answer.message != render.BOX_MESSAGES["unreachable"]
    assert "password" in answer.message


def test_no_message_shown_for_a_failure_quotes_the_far_end():
    """Every sentence the screen can show is written here, chosen by a code.

    The same rule ``/api/health`` and the endpoint check follow: nothing the box
    composed reaches the browser.
    """
    box = FakeBox(error=EndpointUnreachable("connection refused to 100.64.1.2:8080"))

    answer = ask("what dose of perindopril am I on", box)

    assert "100.64" not in answer.message
    assert answer.message in render.BOX_MESSAGES.values()


# -- the prompt --------------------------------------------------------------


def test_the_answering_call_is_grammar_constrained_and_bounded():
    box = FakeBox(sentences=[{"text": "You take perindopril 5mg daily.", "source": "a3f91c"}])

    ask("what dose of perindopril am I on", box)

    call = box.answering_calls[0]
    assert call["schema_name"] == prompts.ANSWER_SCHEMA_NAME
    assert call["max_tokens"] == prompts.ANSWER_MAX_TOKENS


def test_exactly_one_generation_per_question():
    box = FakeBox(sentences=[{"text": "You take perindopril 5mg daily.", "source": "a3f91c"}])

    ask("what dose of perindopril am I on", box)

    assert len(box.answering_calls) == 1


def test_the_prompt_carries_only_retrieved_extracts():
    box = FakeBox(sentences=[{"text": "You take perindopril 5mg daily.", "source": "a3f91c"}])

    answer = ask("what dose of perindopril am I on", box)

    sent = box.answering_calls[0]["text"]
    for passage in answer.retrieval.passages:
        assert passage.key in sent


def test_no_image_ever_reaches_this_path():
    """A question is text. The vision path is for reading artefacts."""
    box = FakeBox(sentences=[{"text": "You take perindopril 5mg daily.", "source": "a3f91c"}])

    ask("what dose of perindopril am I on", box)

    for call in box.calls:
        for message in call["messages"]:
            assert isinstance(message["content"], str)
