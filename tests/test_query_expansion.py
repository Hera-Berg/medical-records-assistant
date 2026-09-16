"""Phase 10: one expansion round, and the model never steers file access.

The expansion exists because people ask in their own words — "what I take for
fluid retention", "the heart letter" — and the record is written in the
pharmacy's. One round is allowed to bridge that: the model proposes search
*terms* and code retrieves against them.

Two things are being pinned here.

**It is exactly one round.** Not one by convention — one because it is a
function call with no loop around it. CLAUDE.md: "If this ever starts wanting a
tool loop, that is a signal the retrieval layer is too weak, not that the system
needs a planner."

**The model cannot name a file.** The vault is synced by a third party, so its
contents are untrusted input, and a model must never choose what gets read next.
What comes back is folded to plain words before it is used, which removes ``/``,
``\\`` and ``.`` outright — so a term written to look like a path arrives as the
words in it and matches nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent import query
from agent.query import expand, prompts

from .conftest import FakeBox, claim, confirm, ingested, on_day

DEVICE = "laptop-a1b2"
AS_OF = datetime(2026, 9, 16, tzinfo=timezone.utc)


def a_record():
    events = [ingested(DEVICE, "a3f91c", ts=on_day(2))]
    dose = claim(DEVICE, "med:furosemide", "dose", "40mg in the morning", ts=on_day(3),
                 artifact="a3f91c", occurred={"value": "2026-09-02", "precision": "day"})
    events += [dose, confirm(DEVICE, dose.id, ts=on_day(3, hour=12))]
    return query.Record.of(sorted(events, key=lambda e: e.sort_key), AS_OF)


# -- when it runs ------------------------------------------------------------


def test_a_thin_retrieval_gets_one_expansion():
    box = FakeBox(
        terms=["furosemide"],
        sentences=[{"text": "You take furosemide 40mg in the morning.", "source": "a3f91c"}],
    )

    answer = query.ask("what am I on for fluid retention", a_record(), client=box)

    assert len(box.term_calls) == 1
    assert answer.expanded_terms == ("furosemide",)
    assert answer.state == "answered"


def test_a_full_retrieval_does_not_expand_at_all():
    box = FakeBox(
        terms=["something"],
        sentences=[{"text": "You take furosemide 40mg in the morning.", "source": "a3f91c"}],
    )

    query.ask("what dose of furosemide am I on", a_record(), client=box)

    assert box.term_calls == []


def test_the_expansion_happens_once_and_never_again():
    """Even when the expanded retrieval is still thin.

    A second round is what a planner looks like from the inside. There is no
    code path to one: the expansion is a call, and the call site runs once.
    """
    box = FakeBox(terms=["nothing-matches-this"], sentences=[])

    query.ask("what am I on for fluid retention", a_record(), client=box)

    assert len(box.term_calls) == 1


def test_with_no_box_there_is_no_expansion_and_retrieval_still_answers():
    answer = query.ask("what dose of furosemide am I on", a_record(), box="unreachable")

    assert answer.expanded_terms == ()
    assert answer.retrieval.passages


# -- what it is allowed to return --------------------------------------------


def test_a_proposed_path_is_folded_into_words_and_names_no_file():
    assert expand.clean_terms(["../../events/2026-09.laptop.jsonl"]) == (
        "events 2026 09 laptop jsonl",
    )
    assert expand.clean_terms(["/etc/passwd"]) == ("etc passwd",)
    assert expand.clean_terms(["raw/2026/09/photo.jpg"]) == ("raw 2026 09 photo jpg",)


def test_terms_are_capped_in_number_and_length():
    proposed = [f"term-number-{index}" for index in range(50)]
    assert len(expand.clean_terms(proposed)) <= prompts.MAX_TERMS

    long_one = expand.clean_terms(["x" * 400])
    assert all(len(term) <= prompts.MAX_TERM_CHARS for term in long_one)


def test_anything_that_is_not_a_string_is_dropped():
    assert expand.clean_terms([None, 3, {"a": 1}, ["b"], "aspirin"]) == ("aspirin",)
    assert expand.clean_terms("not a list") == ()
    assert expand.clean_terms(None) == ()


def test_an_unusable_expansion_answer_costs_nothing():
    """A box that answers the expansion badly must not fail the question."""
    box = FakeBox(sentences=[{"text": "You take furosemide 40mg.", "source": "a3f91c"}])
    box.terms = ()

    answer = query.ask("what am I on for fluid retention", a_record(), client=box)

    assert answer.expanded_terms == ()
    # The original retrieval stands, and the question is still answered from it.
    assert answer.state in {"answered", "empty"}


# -- what it is shown --------------------------------------------------------


def test_the_expansion_prompt_carries_page_titles_and_no_record_text():
    """It sees names, never anything read out of a document.

    A tampered artefact must not be able to put words in front of the model that
    steer what gets retrieved next. These names are the titles of wiki pages.
    """
    box = FakeBox(terms=["furosemide"], sentences=[])

    query.ask("what am I on for fluid retention", a_record(), client=box)

    sent = box.term_calls[0]["text"]
    assert "Furosemide" in sent
    assert "40mg in the morning" not in sent
    assert "a3f91c" not in sent


def test_the_expansion_call_is_schema_constrained_and_small():
    box = FakeBox(terms=["furosemide"], sentences=[])

    query.ask("what am I on for fluid retention", a_record(), client=box)

    call = box.term_calls[0]
    assert call["schema_name"] == prompts.TERMS_SCHEMA_NAME
    assert call["max_tokens"] == prompts.TERMS_MAX_TOKENS


def test_a_refused_question_never_reaches_the_box_at_all():
    """Not even for terms. The refusal is before retrieval, so it is before this."""
    box = FakeBox(terms=["anything"], sentences=[])

    query.ask("should I be worried about what I take for fluid retention", a_record(), client=box)

    assert box.calls == []
