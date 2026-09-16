"""Phase 10: retrieval is deterministic code, and it is bounded.

Every question shape in the brief gets a test here, against a record built from
events rather than from mocks — the point of deterministic retrieval is that it
can be checked exactly, and a fake projection would be checking the fake.

The two properties that matter beyond "it finds the right thing":

**It is reproducible.** The same question against the same record selects the
same passages in the same order. That is the projection's own guarantee extended
to retrieval, and it is what makes an answer checkable a second time.

**It is bounded.** No question, however broad, drags the whole record into a
prompt. ``MODELS.md``: "If a prompt exceeds the cap, that is a retrieval bug,
not a reason to raise the cap."
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent import query
from agent.query import classify as classify_mod
from agent.query import context as context_mod
from agent.query import retrieve as retrieve_mod

from .conftest import claim, confirm, correct, ingested, note, on_day

DEVICE = "laptop-a1b2"
AS_OF = datetime(2026, 9, 16, tzinfo=timezone.utc)


def record(events):
    return query.Record.of(sorted(events, key=lambda e: e.sort_key), AS_OF)


def a_record():
    """A small record with one of most things in it."""
    events = [
        ingested(DEVICE, "a3f91c", ts=on_day(2)),
        ingested(DEVICE, "77b210", ts=on_day(3), mime="application/pdf"),
        ingested(DEVICE, "cc1234", ts=on_day(4), mime="audio/webm", source="recorder"),
    ]
    dose = claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(5),
                 artifact="a3f91c", occurred={"value": "2026-09-04", "precision": "day"})
    events += [dose, confirm(DEVICE, dose.id, ts=on_day(5, hour=12))]

    allergy = claim(DEVICE, "allergy:penicillin", "reaction", "rash", ts=on_day(6),
                    artifact="77b210", occurred={"value": "2026-09-05", "precision": "day"})
    events += [allergy, confirm(DEVICE, allergy.id, ts=on_day(6, hour=12))]

    person = claim(DEVICE, "person:dr-nguyen", "role", "Cardiologist", ts=on_day(3),
                   artifact="77b210", occurred={"value": "2026-09-03", "precision": "day"})
    events += [person, confirm(DEVICE, person.id, ts=on_day(3, hour=12))]

    statin = claim(DEVICE, "med:atorvastatin", "dose", "40mg daily", ts=on_day(7),
                   artifact="77b210", occurred={"value": "2026-09-06", "precision": "day"})
    events += [statin, confirm(DEVICE, statin.id, ts=on_day(7, hour=12))]

    events.append(
        note(DEVICE, ts=on_day(8), text="The headaches have been better since the tablets changed.")
    )
    return record(events)


def ask(question, built=None, **kwargs):
    return query.ask(question, built or a_record(), box="unreachable", **kwargs)


def lines(answer):
    return [passage.line for passage in answer.retrieval.passages]


# -- the shapes --------------------------------------------------------------


def test_an_entity_is_found_by_name():
    answer = ask("what dose of perindopril am I on")

    assert any("Perindopril — dose: 5mg daily" in line for line in lines(answer))


def test_a_kind_question_finds_the_whole_shelf_and_nothing_else():
    answer = ask("what allergies do I have")

    found = lines(answer)
    assert any("Penicillin" in line for line in found)
    assert not any("Perindopril" in line for line in found)


def test_a_person_question_finds_what_their_documents_say():
    """"What did Dr Nguyen recommend" is answered by the letter they appear in.

    The practitioner is an entity, the documents naming them are the artefacts
    their claims were read off, and everything else read off those documents is
    what the question is actually asking about. No model call reaches that.
    """
    answer = ask("what did Dr Nguyen recommend")

    found = lines(answer)
    assert any("Dr Nguyen" in line for line in found)
    assert any("Atorvastatin" in line for line in found), found
    assert any("Penicillin" in line for line in found), found


def test_a_predicate_question_finds_the_slot_across_entities():
    answer = ask("what doses am I on")

    found = lines(answer)
    assert any("Perindopril — dose" in line for line in found)
    assert any("Atorvastatin — dose" in line for line in found)


def test_a_recency_question_finds_the_rows_in_that_window():
    answer = ask("what happened in September")

    assert answer.retrieval.question.window is not None
    assert any(passage.kind == retrieve_mod.ROW for passage in answer.retrieval.passages)


def test_a_term_question_matches_free_text_in_the_record():
    answer = ask("what have I said about headaches")

    assert any("headaches" in line.lower() for line in lines(answer))


def test_a_counting_question_is_counted_in_code():
    """The model never produces a number that reaches the person.

    ``MODELS.md`` forbids the model computing anything that reaches the record;
    a number read off the screen is no different, so the count is arithmetic
    over what retrieval matched and the sentence is written here.
    """
    answer = ask("how many times have I mentioned the headaches")

    assert answer.tally == "1 entry in your record mentions what you asked about."


def test_a_question_with_no_match_retrieves_nothing():
    answer = ask("when did I have my appendix out")

    assert answer.retrieval.is_empty
    assert answer.state == "empty"


def test_a_question_naming_a_kind_answers_from_that_kind_even_when_its_words_miss():
    """"what did the surgeon say about my knee" finds the practitioners.

    Nothing in this record is about a knee or a surgeon, but the question names
    a *kind* the record holds, so the honest answer is what that shelf actually
    contains rather than silence. The alternative — treating an unmatched
    adjective as a reason to return nothing — would answer "your record covers
    none of that" about a record that has a practitioner in it.
    """
    answer = ask("what did the surgeon say about my knee")

    assert any("Dr Nguyen" in line for line in lines(answer))


# -- what a passage carries --------------------------------------------------


def test_every_passage_carries_a_citation_that_resolves():
    answer = ask("what am I taking")

    assert answer.retrieval.passages
    for passage in answer.retrieval.passages:
        assert passage.key
        assert passage.citation.text
        assert passage.citation.resolved


def test_a_correction_is_not_attributed_to_a_prescriber():
    """A correction carries the tier it replaced so it can outrank a re-read.

    Printing that tier on the answer would attribute the patient's own typing to
    a prescriber, so the passage says who actually said it.
    """
    events = [ingested(DEVICE, "a3f91c", ts=on_day(2))]
    misread = claim(DEVICE, "med:levothyroxine", "dose", "5Omcg daily", ts=on_day(3),
                    artifact="a3f91c")
    events += [
        misread,
        correct(DEVICE, value="50mcg daily", target=misread.id, ts=on_day(4),
                evidence_tier="prescriber-issued"),
    ]
    answer = ask("what dose of levothyroxine am I on", record(events))

    written = " ".join(lines(answer))
    assert "you corrected this" in written
    assert "50mcg daily" in written


def test_a_conflict_offers_both_readings_and_picks_neither():
    events = [ingested(DEVICE, "a3f91c", ts=on_day(1)), ingested(DEVICE, "b4c5d6", ts=on_day(2))]
    first = claim(DEVICE, "med:atorvastatin", "dose", "20mg daily", ts=on_day(3), artifact="a3f91c",
                  occurred={"value": "2026-09-01", "precision": "day"})
    second = claim(DEVICE, "med:atorvastatin", "dose", "40mg daily", ts=on_day(4), artifact="b4c5d6",
                   occurred={"value": "2026-09-01", "precision": "day"})
    events += [first, confirm(DEVICE, first.id, ts=on_day(3, hour=12)),
               second, confirm(DEVICE, second.id, ts=on_day(4, hour=12))]

    answer = ask("what dose of atorvastatin am I on", record(events))

    written = " ".join(lines(answer))
    assert "20mg daily" in written and "40mg daily" in written
    assert "disagree" in written


# -- bounds ------------------------------------------------------------------


def test_retrieval_is_reproducible():
    built = a_record()
    first = ask("what am I taking", built)
    second = ask("what am I taking", built)

    assert lines(first) == lines(second)


def test_no_question_retrieves_more_than_the_cap():
    events = [ingested(DEVICE, "a3f91c", ts=on_day(1))]
    for index in range(200):
        made = claim(DEVICE, f"med:drug-{index:03d}", "dose", f"{index}mg daily",
                     ts=on_day(2, hour=index % 24), artifact="a3f91c")
        events += [made, confirm(DEVICE, made.id, ts=on_day(3, hour=index % 24))]

    answer = ask("what medications am I taking", record(events))

    assert len(answer.retrieval.passages) <= retrieve_mod.MAX_PASSAGES


def test_a_broad_question_still_fits_the_prompt_cap():
    """The cap is enforced, not hoped for. See MODELS.md on prompt size."""
    events = [ingested(DEVICE, "a3f91c", ts=on_day(1))]
    for index in range(200):
        made = claim(DEVICE, f"med:drug-{index:03d}", "dose", f"{index}mg daily",
                     ts=on_day(2, hour=index % 24), artifact="a3f91c")
        events += [made, confirm(DEVICE, made.id, ts=on_day(3, hour=index % 24))]

    answer = ask("what medications am I taking", record(events))
    built = context_mod.build(answer.retrieval)

    assert built.char_count() < context_mod.MAX_PROMPT_CHARS
    assert built.dropped == 0


def test_the_whole_wiki_is_never_dumped_into_a_prompt():
    built = a_record()
    answer = ask("what dose of perindopril am I on", built)

    subjects = {passage.subject_id for passage in answer.retrieval.passages}
    assert "allergy:penicillin" not in subjects


# -- the record is read through the projection, never the log ----------------


def test_retrieval_reads_the_projection_and_not_the_event_stream():
    """A pending high-consequence claim is not retrievable.

    It is not in the wiki, because the consequence gate has not let it in, and
    an answer that quoted it would be showing somebody a value the record
    deliberately does not yet hold.
    """
    events = [ingested(DEVICE, "a3f91c", ts=on_day(1))]
    events.append(claim(DEVICE, "allergy:sulfonamides", "reaction", "swelling",
                        ts=on_day(2), artifact="a3f91c"))

    answer = ask("what allergies do I have", record(events))

    assert "swelling" not in " ".join(lines(answer))
