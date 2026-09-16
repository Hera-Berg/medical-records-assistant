"""Phase 10: rejected content appears in no answer, under any phrasing.

The deciding case for this rule, stated in CLAUDE.md, is someone who rejects a
mis-OCR'd "alcohol dependence": they "must not find it on their problems page
under any heading, however carefully that heading is worded". An answer is a
worse place for it to surface than a page, because an answer is produced on
demand, by a question aimed straight at it, and is then read aloud or pasted
somewhere.

Two mechanisms, and this file tests both:

**Retrieval reads the projection**, which suppressed the rejected claim before
any slot was built. That is what covers the claim itself.

**Free text carrying a rejected reading's wording is withheld.** A rejection
retracts a claim, not a recording, so a transcript can still contain the words —
legitimately, on the timeline, where the record shows everything it holds. An
answer is a different object and does not get them.

And the assertion that matters most: the rejected wording is in **no byte of the
record sent** to the box. A model cannot leak what it was never given,
which is a stronger guarantee than any instruction in a prompt.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent import query
from agent.query import render

from agent.events import envelope

from .conftest import FakeBox, claim, confirm, ingested, on_day, reject

DEVICE = "laptop-a1b2"
AS_OF = datetime(2026, 9, 16, tzinfo=timezone.utc)

REJECTED = "alcohol dependence"

#: Every way somebody might go looking for it. The rule is "under any phrasing",
#: so the phrasings are written out rather than assumed.
PHRASINGS = [
    "do I have a problem with alcohol",
    "what does my record say about alcohol",
    "am I recorded as alcohol dependent",
    "list my problems",
    "what problems do I have",
    "what did the voice note say",
    "what is in my record about dependence",
    "tell me everything in my record",
]


def a_record(with_transcript: bool = False):
    """A record holding one rejected reading, and something else that is fine."""
    events = [
        ingested(DEVICE, "a3f91c", ts=on_day(2)),
        ingested(DEVICE, "cc1234", ts=on_day(3), mime="audio/webm", source="recorder"),
    ]
    kept = claim(DEVICE, "problem:hypertension", "name", "Hypertension", ts=on_day(4),
                 artifact="a3f91c", occurred={"value": "2026-09-02", "precision": "day"})
    events += [kept, confirm(DEVICE, kept.id, ts=on_day(4, hour=12))]

    misheard = claim(DEVICE, "problem:alcohol-dependence", "name", REJECTED, ts=on_day(5),
                     artifact="cc1234", tier="patient-reported",
                     occurred={"value": "2026-09-03", "precision": "day"},
                     subject_name="alcohol dependence")
    events += [misheard, reject(DEVICE, misheard.id, ts=on_day(6))]

    if with_transcript:
        # What the speech model typed up from the recording. The timeline shows
        # this on the recording's own row, legitimately — the rejection retracted
        # a claim, not the fact that the words were said.
        events.append(
            envelope.new(
                "extraction.completed",
                DEVICE,
                ts=on_day(5, hour=11),
                payload={
                    "artifact": "cc1234",
                    "transcript": f"I told her about the {REJECTED} thing, which was wrong.",
                },
                provenance={"model": "faster-whisper-small", "artifact": "cc1234"},
            )
        )
    return query.Record.of(sorted(events, key=lambda e: e.sort_key), AS_OF)


@pytest.mark.parametrize("question", PHRASINGS)
def test_a_rejected_reading_is_in_no_answer_however_it_is_asked_for(question):
    box = FakeBox(sentences=[{"text": "Your record lists hypertension.", "source": "a3f91c"}])

    answer = query.ask(question, a_record(), client=box)

    assert REJECTED not in answer.text.lower()
    assert REJECTED not in answer.message.lower()
    for passage in (answer.retrieval.passages if answer.retrieval else ()):
        assert REJECTED not in passage.line.lower()


@pytest.mark.parametrize("question", PHRASINGS)
def test_the_rejected_wording_is_in_no_byte_of_any_request(question):
    """The strongest form of the rule: the model is never given it.

    An instruction in a prompt is a request. Not sending the words at all is a
    guarantee, and it is the one that still holds when the model misbehaves.
    """
    box = FakeBox(sentences=[{"text": "Your record lists hypertension.", "source": "a3f91c"}])

    query.ask(question, a_record(), client=box)

    assert REJECTED not in box.record_sent().lower()


def test_a_transcript_repeating_the_rejected_words_is_withheld_too():
    """The second mechanism, and the one the projection does not cover.

    The recording said the words and the timeline still shows that. This is a
    different object: it is produced on demand by a question aimed at it, and it
    gets read aloud.
    """
    built = a_record(with_transcript=True)
    box = FakeBox(sentences=[{"text": "Your record lists hypertension.", "source": "a3f91c"}])

    answer = query.ask("what did I say in the voice note", built, client=box)

    assert REJECTED not in box.record_sent().lower()
    assert answer.retrieval.withheld >= 1


def test_the_withholding_is_silent():
    """Saying "one entry was withheld" would point straight at it."""
    built = a_record(with_transcript=True)
    box = FakeBox(sentences=[{"text": "Your record lists hypertension.", "source": "a3f91c"}])

    answer = query.ask("what did I say in the voice note", built, client=box)

    assert "withheld" not in answer.message.lower()
    assert "rejected" not in answer.message.lower()


def test_the_model_cannot_reintroduce_it_by_citing_a_real_source():
    """Even a well-cited sentence carrying it is a sentence about a retraction.

    It cannot arise — the words were never sent — but if it did, the sentence
    would still have to cite a passage, and no passage carries them.
    """
    box = FakeBox(
        sentences=[{"text": f"Your record mentions {REJECTED}.", "source": "a3f91c"}]
    )

    answer = query.ask("what problems do I have", a_record(), client=box)

    # The sentence cites a real key, so it survives validation — which is
    # exactly why the words must never have been in front of the model.
    assert answer.state == render.ANSWERED
    assert box.record_sent().lower().count(REJECTED) == 0


def test_an_unrejected_reading_of_the_same_words_from_another_document_is_not_suppressed():
    """A rejection is keyed on the artefact, and the withholding must not be broader.

    A *different* document saying the same thing is new evidence and the user's
    to decide again — the settled decision that keeps the artefact in the
    suppression key. Withholding on wording alone would quietly hide it.
    """
    events = [
        ingested(DEVICE, "a3f91c", ts=on_day(2)),
        ingested(DEVICE, "dd5678", ts=on_day(3), mime="application/pdf"),
    ]
    first = claim(DEVICE, "problem:tinnitus", "name", "persistent tinnitus", ts=on_day(4),
                  artifact="a3f91c", tier="patient-reported")
    events += [first, reject(DEVICE, first.id, ts=on_day(5))]

    second = claim(DEVICE, "problem:tinnitus", "name", "persistent tinnitus", ts=on_day(6),
                   artifact="dd5678", occurred={"value": "2026-09-05", "precision": "day"})
    events += [second, confirm(DEVICE, second.id, ts=on_day(7))]

    built = query.Record.of(sorted(events, key=lambda e: e.sort_key), AS_OF)
    answer = query.ask("what problems do I have", built, box="unreachable")

    found = " ".join(passage.line for passage in answer.retrieval.passages)
    assert "tinnitus" in found.lower()
