"""Phase 10: a citation has to cover the join, not just the facts either side.

"What am I taking for my blood pressure" is the question this file exists for.
The record holds a medication list. It does **not** hold what any of those
medications is for — there is no ``indication`` predicate yet, and reading one
off a script is its own piece of work. So the honest answer names the
medications and says the record does not record what each is for.

The sentence that must never appear is:

    "You take perindopril 5mg daily for your blood pressure." [a3f91c]

Every word of that is defensible except the one that matters. The drug and the
dose came off the photograph; the word *for* came from what the model knows
about perindopril. It is worse than an uncited sentence, because an uncited
sentence is dropped and gone while this one arrives looking sourced, reads as
sourced, and asserts the one thing the record never said.

The rule is enforced in code and needs no second version for the day the record
does hold an indication: on that day the extract carries those words, and
nothing is refused.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent import query
from agent.query import classify as classify_mod
from agent.query import render, retrieve as retrieve_mod

from .conftest import FakeBox, claim, confirm, ingested, on_day

DEVICE = "laptop-a1b2"
AS_OF = datetime(2026, 9, 16, tzinfo=timezone.utc)


def a_record(with_indication: bool = False):
    """A medication list, with or without anything saying what it is for."""
    events = [ingested(DEVICE, "a3f91c", ts=on_day(1))]
    dose = claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(2),
                 artifact="a3f91c", occurred={"value": "2026-09-01", "precision": "day"})
    events += [dose, confirm(DEVICE, dose.id, ts=on_day(2, hour=12))]
    statin = claim(DEVICE, "med:atorvastatin", "dose", "40mg daily", ts=on_day(3),
                   artifact="a3f91c", occurred={"value": "2026-09-01", "precision": "day"})
    events += [statin, confirm(DEVICE, statin.id, ts=on_day(3, hour=12))]

    if with_indication:
        # What the record will hold once the indication predicate exists. The
        # claim is hand-authored here rather than extracted, which is exactly
        # what a test of *this* layer should do: the reading of it is another
        # piece of work, and this one has to be right before and after it.
        told = claim(DEVICE, "med:perindopril", "indication", "for blood pressure",
                     ts=on_day(4), artifact="a3f91c",
                     occurred={"value": "2026-09-01", "precision": "day"})
        events += [told, confirm(DEVICE, told.id, ts=on_day(4, hour=12))]
    return query.Record.of(sorted(events, key=lambda e: e.sort_key), AS_OF)


THE_QUESTION = "what am I taking for my blood pressure"


# -- the question is recognised as one about purpose -------------------------


def test_the_question_is_read_as_asking_what_something_is_for():
    assert classify_mod.purpose_of(THE_QUESTION) == "my blood pressure"
    assert classify_mod.purpose_of("what am I on for fluid retention") == "fluid retention"
    assert classify_mod.purpose_of("which tablet treats my thyroid") == "my thyroid"


def test_things_that_are_not_purposes_are_not_read_as_purposes():
    for question in (
        "how long have I been on it for",
        "what am I taking for now",
        "what did the letter say for June",
        "when did I start perindopril",
    ):
        assert classify_mod.purpose_of(question) == "", question


# -- the join is refused -----------------------------------------------------


def test_the_record_holds_no_indication_and_says_so():
    answer = query.ask(THE_QUESTION, a_record(), box="unreachable")

    assert answer.retrieval.purpose == "my blood pressure"
    assert not answer.retrieval.indication_supported
    assert answer.retrieval.unsupported_purpose == ("blood", "pressure")
    assert answer.note == (
        "Your record does not say what each medicine is for, so nothing here "
        "can tell you which of them is for my blood pressure. It can only say "
        "what you take."
    )


def test_a_sentence_that_supplies_the_link_is_dropped():
    """The whole point. It cites a real extract and is still not sourced."""
    box = FakeBox(
        sentences=[
            {"text": "You take perindopril 5mg daily.", "source": "a3f91c"},
            {
                "text": "You take perindopril 5mg daily for your blood pressure.",
                "source": "a3f91c",
            },
        ]
    )

    answer = query.ask(THE_QUESTION, a_record(), client=box)

    assert [sentence.text for sentence in answer.sentences] == [
        "You take perindopril 5mg daily."
    ]
    assert answer.dropped == 1
    assert "blood pressure" not in answer.text


def test_the_dropped_link_is_gone_rather_than_hedged():
    box = FakeBox(
        sentences=[
            {"text": "Perindopril is the one for blood pressure.", "source": "a3f91c"},
        ]
    )

    answer = query.ask(THE_QUESTION, a_record(), client=box)

    assert answer.sentences == ()
    assert "blood pressure" not in answer.text
    # And the reason the answer is empty is said, not left to be inferred from
    # an empty space where an answer should be.
    assert answer.note


def test_the_medications_are_still_named():
    """Refusing the join is not refusing the question.

    The honest answer to "what am I taking for my blood pressure" is the
    medication list plus what the record does not hold. Withholding the list as
    well would be answering a different, worse question.
    """
    box = FakeBox(
        sentences=[
            {"text": "You take perindopril 5mg daily.", "source": "a3f91c"},
            {"text": "You take atorvastatin 40mg daily.", "source": "a3f91c"},
        ]
    )

    answer = query.ask(THE_QUESTION, a_record(), client=box)

    assert len(answer.sentences) == 2
    assert "perindopril" in answer.text.lower()
    assert "atorvastatin" in answer.text.lower()
    assert answer.note


def test_only_the_purpose_words_are_policed():
    """Ordinary rephrasing is not the failure and is not touched.

    A rule that every word of a sentence had to appear in its source would
    delete the model's job. What is refused is asserting a connection the record
    does not hold.
    """
    box = FakeBox(
        sentences=[
            {
                "text": "Your current dose of perindopril is 5 mg, taken once each day.",
                "source": "a3f91c",
            }
        ]
    )

    answer = query.ask(THE_QUESTION, a_record(), client=box)

    assert len(answer.sentences) == 1
    assert answer.dropped == 0


# -- and what happens on the day the record does hold one --------------------


def test_an_indication_in_the_record_makes_the_link_sayable():
    """The same rule, unchanged, once the record actually holds the fact.

    Nothing here is special-cased for that day: the extract carries the words,
    so the sentence citing it is not borrowing anything.
    """
    box = FakeBox(
        sentences=[
            {
                "text": "You take perindopril 5mg daily for blood pressure.",
                "source": "a3f91c",
            }
        ]
    )

    answer = query.ask(THE_QUESTION, a_record(with_indication=True), client=box)

    assert [s.text for s in answer.sentences] == [
        "You take perindopril 5mg daily for blood pressure."
    ]
    assert answer.dropped == 0
    assert answer.note == "", "nothing to say about a gap the record has filled"


def test_an_indication_is_retrieved_and_recognised():
    answer = query.ask(THE_QUESTION, a_record(with_indication=True), box="unreachable")

    assert answer.retrieval.indication_supported
    assert answer.retrieval.unsupported_purpose == ()
    assert any(
        passage.predicate == retrieve_mod.INDICATION
        for passage in answer.retrieval.passages
    )


# -- the note is written by code, not by the model ---------------------------


def test_the_note_is_not_printed_without_medications_beside_it():
    """It is an answer next to a list and a non-sequitur on its own."""
    empty = query.Record.of([], AS_OF)

    answer = query.ask(THE_QUESTION, empty, box="unreachable")

    assert answer.note == ""
    assert answer.state == render.EMPTY


def test_the_note_echoes_the_persons_own_words_and_caps_them():
    long_purpose = "for my " + ("extremely " * 40) + "high blood pressure"
    box = FakeBox(sentences=[{"text": "You take perindopril.", "source": "a3f91c"}])

    answer = query.ask(f"what am I taking {long_purpose}", a_record(), client=box)

    assert len(answer.note) < len(long_purpose)
    assert render.MAX_PURPOSE_CHARS == 60


@pytest.mark.parametrize(
    "question",
    [
        "what am I taking for my blood pressure",
        "what do I take for my heart",
        "which of my tablets is for cholesterol",
        "what am I on for my thyroid",
    ],
)
def test_no_phrasing_of_the_question_gets_an_unsupported_link(question):
    box = FakeBox(
        sentences=[
            {"text": "Perindopril is the one you take for that.", "source": "a3f91c"},
            {"text": "You take perindopril 5mg daily.", "source": "a3f91c"},
        ]
    )

    answer = query.ask(question, a_record(), client=box)

    purpose = classify_mod.purpose_of(question)
    for word in answer.retrieval.unsupported_purpose:
        assert word not in answer.text.lower(), (question, purpose, word)
