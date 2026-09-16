"""Phase 10: the prompt cap, and what yields when it is tight.

``MODELS.md``: "Cap the prompt at 8–16K and retrieve narrowly … If a prompt
exceeds the cap, that is a retrieval bug, not a reason to raise the cap." So the
cap is enforced here rather than hoped for, and the order things yield in is the
design:

**History goes first, whole.** The current question's evidence outranks the
conversation's memory — a follow-up that loses its history still answers from
the record, while one that loses the record answers from nothing. Half a
conversation is worse than none: a question answered against the wrong earlier
turn is answered wrongly and confidently.

**Then passages, worst-ranked first**, and whole ones. Half a passage is a dose
with no unit or a date with no year, and a model reading one will complete it
plausibly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent import query
from agent.query import context as context_mod
from agent.query.classify import Turn
from agent.query.retrieve import Passage, Retrieval

from .conftest import FakeBox, claim, confirm, ingested, on_day

DEVICE = "laptop-a1b2"
AS_OF = datetime(2026, 9, 16, tzinfo=timezone.utc)


def a_record(drugs: int = 1):
    events = [ingested(DEVICE, "a3f91c", ts=on_day(1))]
    for index in range(drugs):
        made = claim(DEVICE, f"med:drug-{index:03d}", "dose", f"{index}mg daily",
                     ts=on_day(2, hour=index % 24), artifact="a3f91c")
        events += [made, confirm(DEVICE, made.id, ts=on_day(3, hour=index % 24))]
    return query.Record.of(sorted(events, key=lambda e: e.sort_key), AS_OF)


#: A passage whose rendered line fills the budget bar a little. Big enough that
#: adding any history pushes it over, small enough to fit on its own — which is
#: the state where the order of yielding is observable at all.
def almost_the_whole_budget() -> Passage:
    return passage(context_mod.MAX_PROMPT_CHARS - context_mod.RESERVED_CHARS - 100)


def passage(size: int, rank: int = 0) -> Passage:
    from agent.projection.citations import Citation

    return Passage(
        key="a3f91c",
        kind="fact",
        event_id=f"e{rank}-{size}",
        subject_id="med:x",
        title="X (medication) — dose",
        text="y" * size,
        predicate="dose",
        tier="prescriber-issued",
        corrected=False,
        when="",
        citation=Citation(key="a3f91c", text="Photograph", target="raw/x.jpg"),
        rank=rank,
        order=("med:x", "dose", rank),
        facet="entity",
    )


# -- the cap -----------------------------------------------------------------


def test_the_cap_is_inside_the_band_models_md_sets():
    assert 8_000 <= context_mod.PROMPT_TOKEN_CAP <= 16_000


def test_a_prompt_that_cannot_be_trimmed_raises_rather_than_being_sent():
    """The cap is not advisory, and going over it is a bug upstream.

    Raising here is what makes "if a prompt exceeds the cap, that is a retrieval
    bug" true rather than a note somebody wrote down. It cannot be reached
    through :func:`agent.query.retrieve.retrieve`, which is the point.
    """
    huge = Retrieval(passages=(passage(context_mod.MAX_PROMPT_CHARS * 2),))

    with pytest.raises(context_mod.ContextTooLarge) as caught:
        context_mod.build(huge)

    assert "retrieval bug" in str(caught.value)
    assert "larger cap" in str(caught.value)


def test_the_last_passage_is_never_dropped():
    """An empty context is not a smaller context; it is a different answer.

    Dropping every passage would produce an answer with nothing it could cite,
    which arrives on screen looking exactly like "your record does not cover
    that" and is nothing of the sort.
    """
    only = passage(context_mod.MAX_PROMPT_CHARS * 2)

    with pytest.raises(context_mod.ContextTooLarge):
        context_mod.build(Retrieval(passages=(only,)))


def test_passages_are_dropped_whole_and_worst_ranked_first():
    keep = passage(200, rank=0)
    drop = passage(context_mod.MAX_PROMPT_CHARS, rank=9)

    built = context_mod.build(Retrieval(passages=(keep, drop)))

    assert built.dropped == 1
    assert [p.event_id for p in built.passages] == [keep.event_id]
    # Whole, not truncated: a half-written dose is worse than a missing one.
    assert keep.text in built.extracts
    assert drop.text not in built.extracts


def test_no_real_question_comes_anywhere_near_the_cap():
    answer = query.ask("what medications am I taking", a_record(drugs=300), box="unreachable")
    built = context_mod.build(answer.retrieval)

    assert built.dropped == 0
    assert built.char_count() < context_mod.MAX_PROMPT_CHARS


# -- history -----------------------------------------------------------------


def test_history_reaches_the_prompt():
    built = context_mod.build(
        Retrieval(passages=(passage(50),)),
        [Turn("what dose of perindopril am I on", "You take 5mg daily.")],
    )

    assert "what dose of perindopril am I on" in built.history
    assert "You take 5mg daily." in built.history


def test_history_yields_before_a_single_passage_does():
    """The current question's evidence outranks the conversation's memory."""
    big = almost_the_whole_budget()

    built = context_mod.build(
        Retrieval(passages=(big,)),
        [Turn("an earlier question", "an earlier answer")],
    )

    assert built.dropped_history == 1
    assert built.history == ""
    assert built.dropped == 0, "the record yielded before the conversation did"


def test_history_is_dropped_whole_rather_than_halved():
    big = almost_the_whole_budget()

    built = context_mod.build(
        Retrieval(passages=(big,)),
        [Turn("first question", "first answer"), Turn("second question", "second answer")],
    )

    assert built.history == ""
    assert "first question" not in built.extracts
    assert "second question" not in built.extracts


def test_only_the_last_two_turns_are_rendered():
    built = context_mod.build(
        Retrieval(passages=(passage(50),)),
        [Turn(f"question {n}", f"answer {n}") for n in range(6)],
    )

    assert "question 5" in built.history
    assert "question 4" in built.history
    assert "question 3" not in built.history


def test_a_turn_whose_answer_was_dropped_carries_no_content_forward():
    """Only what was actually shown is carried forward.

    A sentence dropped for lacking a citation was never put in front of the
    person, and it must not reach the model as though it had been said.
    """
    built = context_mod.build(
        Retrieval(passages=(passage(50),)),
        [Turn("what did the letter say", "")],
    )

    assert "nothing in the record covered it" in built.history


def test_the_whole_conversation_still_fits_a_real_prompt():
    box = FakeBox(sentences=[{"text": "You take drug 0mg daily.", "source": "a3f91c"}])
    record = a_record(drugs=50)

    history = [Turn("what am I taking", "a long answer " * 200)]
    query.ask("and the doses", record, client=box, history=history)

    sent = box.answering_calls[0]["text"]
    assert len(sent) < context_mod.MAX_PROMPT_CHARS
