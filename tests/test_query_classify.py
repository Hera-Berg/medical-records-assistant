"""Phase 10: what a question is asking for, and what it is refused for.

The refusal table is the part of this feature most likely to go quietly wrong,
and it can go wrong in two directions. Letting an interpretation question
through produces the one output this project exists to prevent — a small model
telling somebody whether their result is worrying. Refusing a record question
produces a feature nobody can use, and the phrasings are close enough together
that both failures fit in the same sentence:

    "is my thyroid result serious"        must be refused
    "what am I taking for my blood pressure"   must not be

So every refused phrasing below sits beside the record question it is nearest
to, and both are asserted. Nothing here calls a model — the classifier is
ordinary code and the point of putting the line at the question is that it can
be read, argued with and tested like ordinary code.
"""

from __future__ import annotations

from datetime import date

import pytest

from agent.query import classify as classify_mod
from agent.query.classify import Turn, classify, refusal_for, window_for
from agent.query.vocab import Vocabulary

TODAY = date(2026, 9, 16)


class _Entity:
    """The two fields the vocabulary reads, without building a projection."""

    def __init__(self, kind: str, slug: str, name: str):
        self.subject = type("S", (), {"kind": kind, "slug": slug, "id": f"{kind}:{slug}"})()
        self.name = name
        self.salt_names = ()
        self.merged_into = None
        self.merged_from = ()

    @property
    def id(self) -> str:
        return self.subject.id


def vocabulary() -> Vocabulary:
    entities = {
        "med:perindopril": _Entity("med", "perindopril", "Perindopril"),
        "med:atorvastatin": _Entity("med", "atorvastatin", "Atorvastatin"),
        "person:dr-nguyen": _Entity("person", "dr-nguyen", "Dr Nguyen"),
        "allergy:penicillin": _Entity("allergy", "penicillin", "Penicillin"),
    }
    return Vocabulary.of(entities)


def parse(question: str, history=()):
    return classify(question, vocabulary(), TODAY, history)


# -- the refusal, and the questions it must not catch ------------------------

REFUSED = [
    ("what does TSH mean", classify_mod.MEANING),
    ("what is the meaning of my result", classify_mod.MEANING),
    ("explain what my thyroid reading means", classify_mod.MEANING),
    ("what is a normal range for blood pressure", classify_mod.MEANING),
    ("is my thyroid result serious", classify_mod.SERIOUSNESS),
    ("should I be worried about the statin", classify_mod.SERIOUSNESS),
    ("is this dose too high", classify_mod.SERIOUSNESS),
    ("how serious is the hypertension", classify_mod.SERIOUSNESS),
    ("is 40mg a lot", classify_mod.SERIOUSNESS),
    ("what should I do about the headaches", classify_mod.ADVICE),
    ("can I stop taking the statin", classify_mod.ADVICE),
    ("should I take both scripts", classify_mod.ADVICE),
    ("is it safe to double the dose", classify_mod.ADVICE),
    ("what happens if I skip a day", classify_mod.ADVICE),
    ("does perindopril interact with metformin", classify_mod.ADVICE),
    ("why do I have high blood pressure", classify_mod.CAUSE),
    ("what causes the headaches", classify_mod.CAUSE),
    ("could this be a side effect", classify_mod.CAUSE),
    ("what do you think about the two statin scripts", classify_mod.OPINION),
    ("what would you do", classify_mod.OPINION),
    ("what are the side effects of perindopril", classify_mod.GENERAL),
]

#: The record questions that live closest to the refusals above. Each one is a
#: legitimate question about what the folder holds, and refusing it would make
#: the feature useless at the thing it is for.
ALLOWED = [
    "what am I taking for my blood pressure",
    "what did Dr Nguyen recommend",
    "what did the letter say about the statin",
    "do I have a penicillin allergy",
    "what is my dose of perindopril",
    "when did I start perindopril",
    "what did the blood test say",
    "how many times have I mentioned the headaches",
    "what changed since June",
    "what am I being treated for",
    "what is my diagnosis",
    "which doctors are in my record",
    "did anything say to stop the metformin",
    "when was my last script",
    "what does my record say about the headaches",
    "why did the dose change",
]


@pytest.mark.parametrize("question,code", REFUSED)
def test_interpretation_is_refused_before_anything_is_retrieved(question, code):
    parsed = parse(question)

    assert parsed.is_refused, f"{question!r} reached retrieval"
    assert parsed.refusal == code
    # Nothing was parsed out of it: the refusal happens first and the question
    # is not searched for entities on the way past.
    assert not parsed.has_facets
    assert classify_mod.REFUSALS[code]


@pytest.mark.parametrize("question", ALLOWED)
def test_record_questions_are_not_refused(question):
    parsed = parse(question)

    assert not parsed.is_refused, f"{question!r} was refused and is a record question"
    assert parsed.has_facets, f"{question!r} parsed to nothing to retrieve"


def test_a_refusal_never_quotes_the_question_back():
    """Every sentence the screen can show is written in the table, not caught.

    The same rule the endpoint check and ``/api/health`` follow. A refusal that
    interpolated the question could be made to say anything by asking it.
    """
    for code, sentence in classify_mod.REFUSALS.items():
        assert "{" not in sentence and "%" not in sentence
    parsed = parse("is my alcohol dependence serious")
    assert "alcohol" not in classify_mod.REFUSALS[parsed.refusal]


# -- facets ------------------------------------------------------------------


def test_one_question_can_carry_several_facets():
    parsed = parse("what dose of perindopril did Dr Nguyen start me on last year")

    assert "med:perindopril" in parsed.entities
    assert "person:dr-nguyen" in parsed.entities
    assert "dose" in parsed.predicates
    assert parsed.window is not None and parsed.window.label == "2025"
    assert {classify_mod.ENTITY, classify_mod.PERSON, classify_mod.PREDICATE,
            classify_mod.RECENCY} <= parsed.shapes


def test_a_kind_question_names_no_entity():
    parsed = parse("what allergies do I have")

    assert parsed.kinds == ("allergy",)
    assert parsed.entities == ()


def test_an_artefact_question_selects_the_tier_that_identifies_it():
    assert parse("what did the blood test say").tiers == ("lab-issued",)
    assert parse("what did the letter say").tiers == ("prescriber-issued",)
    assert parse("what did I say in the voice note").tiers == ("patient-reported",)


def test_a_salt_variant_resolves_to_the_drug_it_was_filed_under():
    entity = _Entity("med", "perindopril", "Perindopril")
    entity.salt_names = (type("N", (), {"literal": "Perindopril Arginine"})(),)
    vocab = Vocabulary.of({"med:perindopril": entity})

    assert vocab.mentions("how much perindopril arginine am I on") == ("med:perindopril",)


def test_a_longer_name_wins_over_the_shorter_one_inside_it():
    """"perindopril arginine" is one mention, not two."""
    entity = _Entity("med", "perindopril", "Perindopril")
    entity.salt_names = (type("N", (), {"literal": "Perindopril Arginine"})(),)
    vocab = Vocabulary.of({"med:perindopril": entity})

    assert len(vocab.mentions("perindopril arginine")) == 1


# -- date windows ------------------------------------------------------------


def test_windows_are_computed_from_as_of_and_never_from_the_clock():
    assert window_for("what happened last month", TODAY).label == "August 2026"
    assert window_for("what happened last month", date(2026, 1, 9)).label == "December 2025"


def test_in_june_means_the_most_recent_june_never_a_future_one():
    assert window_for("what happened in June", TODAY).start == date(2026, 6, 1)
    # Asked in March, "June" is last June: a record holds what has happened.
    assert window_for("what happened in June", date(2026, 3, 1)).start == date(2025, 6, 1)


def test_may_the_verb_is_not_may_the_month():
    assert window_for("what may be in my record", TODAY) is None
    assert window_for("what did the letter in May say", TODAY).label == "May 2026"
    assert window_for("what happened in May 2025", TODAY).label == "May 2025"


def test_a_span_of_days_is_counted_in_code():
    window = window_for("what happened in the last three months", TODAY)
    assert (window.end - window.start).days == 90


# -- follow-ups --------------------------------------------------------------


def test_a_follow_up_inherits_the_subject_of_the_question_before_it():
    first = parse("what did Dr Nguyen recommend")
    second = parse("when was that", history=[Turn("what did Dr Nguyen recommend", "…", first)])

    assert second.entities == ("person:dr-nguyen",)
    assert second.inherited


def test_a_follow_up_keeps_its_own_slot_rather_than_the_previous_one():
    """"when was that" asks about a date, not about the earlier predicate.

    Inheriting the previous turn's predicate would answer the first question a
    second time, which is the shape of an agent refining its own answer.
    """
    first = parse("what dose of perindopril am I on")
    second = parse("and when did that start", history=[Turn("what dose of perindopril am I on", "…", first)])

    assert second.entities == ("med:perindopril",)
    assert "dose" not in second.predicates


def test_a_question_that_names_its_own_subject_inherits_nothing():
    first = parse("what dose of perindopril am I on")
    second = parse(
        "what about atorvastatin", history=[Turn("what dose of perindopril am I on", "…", first)]
    )

    assert second.entities == ("med:atorvastatin",)


def test_only_the_last_two_turns_are_looked_at():
    assert classify_mod.MAX_TURNS == 3
    assert classify_mod.MAX_PRIOR_TURNS == 2

    oldest = parse("what dose of perindopril am I on")
    history = [
        Turn("what dose of perindopril am I on", "…", oldest),
        Turn("what allergies do I have", "…", parse("what allergies do I have")),
        Turn("and the reactions", "…", parse("and the reactions")),
    ]
    second = parse("when was that", history=history)

    assert "med:perindopril" not in second.entities


def test_a_refused_follow_up_is_still_refused():
    first = parse("what dose of perindopril am I on")
    second = parse("is that a lot", history=[Turn("what dose of perindopril am I on", "…", first)])

    assert second.is_refused


def test_refusal_for_is_the_whole_gate_and_reads_nothing_else():
    """The refusal decision takes a string and nothing else.

    No record, no vocabulary, no snapshot — which is what makes "before
    retrieval" true rather than merely intended.
    """
    assert refusal_for("should I be worried") == classify_mod.SERIOUSNESS
    assert refusal_for("what am I taking") is None
    assert refusal_for("") is None


def test_a_who_question_is_a_question_about_a_person():
    """"Who prescribed them" retrieved nothing at all before this.

    The record holds the practitioner and the letter they wrote, and the answer
    was "nothing in your record covers that" — a record saying it is silent
    about something it is not silent about.
    """
    parsed = parse("and who prescribed them")

    assert "person" in parsed.kinds
    assert classify_mod.PERSON in parsed.shapes
