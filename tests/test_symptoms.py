"""Symptoms: what was said and when, never whether it is happening now.

A symptom is its own kind — ``symptom:cough`` filed under ``wiki/symptoms/`` —
rather than a problem, because a cough is not a diagnosis. Filed as a problem it
gated as adding one, and printed under "active problems" on the sheet handed to
a clinician.

The rules these tests hold the projection to:

**Every mention is kept.** All mentions of one symptom carry the same value, so
the slot leaves one standing and files the rest as superseded; for a symptom
those are the history, and "when did I first mention it" is the oldest.

**Silence never resolves a symptom**, for the same reason silence never stops a
medication. Only a source saying it has gone does, and mentioning it again
after that reports it again.

**Mentions apply at once.** A symptom is low-consequence, so a cough mentioned
an hour ago answers "what symptoms have I had" without waiting a week.

**Quoted words never carry rejected content.** A page quotes the source, and a
quote from an artefact the user rejected any reading of is withheld.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent import projection, query
from agent.extract import families
from agent.extract.schema import PROBLEMS
from agent.projection import entities as entities_mod
from agent.projection import tiers

from .conftest import claim, ingested, on_day, reject

DEVICE = "laptop-a1b2"
AS_OF = datetime(2026, 10, 7, tzinfo=timezone.utc)


def recording(short, day):
    return ingested(
        DEVICE, short, ts=on_day(day), mime="audio/webm", source="recorder",
        captured_ts=on_day(day),
    )


def mention(subject, predicate, name, short, day, said, **extra):
    return claim(
        DEVICE, subject, predicate, name, tier="patient-reported", ts=on_day(day, 10),
        artifact=short, captured_ts=on_day(day), source_span=said, **extra,
    )


def build(events, as_of=AS_OF):
    return projection.project(sorted(events, key=lambda e: e.sort_key), as_of)


def page(result, slug):
    return result.files[f"wiki/symptoms/{slug}.md"].decode("utf-8")


def coughing():
    return [
        recording("aa1111", 3),
        recording("bb2222", 10),
        mention("symptom:cough", "reported", "Cough", "aa1111", 3, "I've had this cough"),
        mention("symptom:cough", "reported", "cough", "bb2222", 10, "still coughing at night"),
    ]


# -- the tier ----------------------------------------------------------------


def test_a_mention_is_low_and_anything_else_about_a_symptom_fails_closed():
    assert tiers.consequence_for("symptom", "reported") == tiers.LOW
    assert tiers.consequence_for("symptom", "resolved") == tiers.LOW
    assert tiers.consequence_for("symptom", "diagnosis") == tiers.HIGH


# -- the page ----------------------------------------------------------------


def test_every_mention_is_listed_with_its_date_and_the_words_used():
    result = build(coughing())
    text = page(result, "cough")
    assert "status: reported" in text
    assert "first_reported: 2026-09-03" in text
    assert "last_reported: 2026-09-10" in text
    assert "Last mentioned 10 September 2026 (recorded); nothing in the record says" in text
    assert "First mentioned 3 September 2026 (recorded)" in text
    assert "“still coughing at night”" in text
    assert "“I've had this cough”" in text
    # Newest first, and no "Earlier readings": a mention is not a replaced value.
    assert text.index("still coughing") < text.index("I've had this cough")
    assert "Earlier readings" not in text


def test_mentions_in_different_words_do_not_conflict():
    """"Cough" and "cough" are one fact said twice, not two readings."""
    result = build(coughing())
    assert result.entities["symptom:cough"].status == entities_mod.REPORTED
    assert not [item for item in result.review if item.kind == "conflict"]


def test_a_mention_applies_without_waiting_for_review():
    events = [recording("aa1111", 3), mention("symptom:cough", "reported", "cough", "aa1111", 3, "cough")]
    result = build(events, as_of=datetime(2026, 9, 3, 11, tzinfo=timezone.utc))
    assert "wiki/symptoms/cough.md" in result.files
    assert not [item for item in result.review if item.subject_id == "symptom:cough"]


def test_a_symptom_is_never_filed_as_a_problem():
    result = build(coughing())
    assert not [path for path in result.files if path.startswith("wiki/problems/")]


# -- silence and resolution --------------------------------------------------


def test_silence_never_resolves_a_symptom():
    years_later = datetime(2029, 1, 1, tzinfo=timezone.utc)
    result = build(coughing(), as_of=years_later)
    assert result.entities["symptom:cough"].status == entities_mod.REPORTED
    assert "status: reported" in page(result, "cough")


def test_a_source_saying_it_has_gone_resolves_it():
    events = coughing() + [
        recording("cc3333", 14),
        mention("symptom:cough", "resolved", "cough", "cc3333", 14, "the cough has cleared up"),
    ]
    result = build(events)
    text = page(result, "cough")
    assert "status: resolved" in text
    assert "resolved_on: 2026-09-14" in text
    assert "Said to have gone — 14 September 2026 (recorded)" in text
    assert "“the cough has cleared up”" in text
    # The history it went away from is still there.
    assert "still coughing at night" in text


def test_mentioning_it_again_after_it_went_reports_it_again():
    events = coughing() + [
        recording("cc3333", 14),
        mention("symptom:cough", "resolved", "cough", "cc3333", 14, "the cough has cleared up"),
        recording("dd4444", 20),
        mention("symptom:cough", "reported", "cough", "dd4444", 20, "the cough is back"),
    ]
    result = build(events)
    assert result.entities["symptom:cough"].status == entities_mod.REPORTED
    assert "last_reported: 2026-09-20" in page(result, "cough")


# -- rejected content --------------------------------------------------------


def test_a_rejected_mention_does_not_render():
    events = coughing()
    events.append(reject(DEVICE, events[-1].id, ts=on_day(11)))
    text = page(build(events), "cough")
    assert "still coughing at night" not in text
    assert "last_reported: 2026-09-03" in text


def test_no_quote_from_an_artefact_the_user_rejected_a_reading_of():
    """The quote can carry more than the symptom. The citation still stands."""
    said = "coughing, and the alcohol dependence again"
    wrong = claim(DEVICE, "problem:alcohol-dependence", "name", "alcohol dependence",
                  tier="patient-reported", ts=on_day(10, 11), artifact="bb2222")
    events = [
        recording("bb2222", 10),
        mention("symptom:cough", "reported", "cough", "bb2222", 10, said),
        wrong,
        reject(DEVICE, wrong.id, ts=on_day(11)),
    ]
    result = build(events)
    text = page(result, "cough")
    assert "alcohol" not in text
    assert "[^bb2222]" in text
    assert not any(b"alcohol" in body for body in result.files.values())


# -- reading -----------------------------------------------------------------


def _problems_answer(**symptom):
    entry = {
        "name": "cough", "gone": None, "evidence_tier": "patient-reported",
        "occurred_at": None, "occurred_span": None,
        "source_span": "I've been coughing", "confidence": 0.9,
    }
    entry.update(symptom)
    return {"problems": [], "symptoms": [entry], "people": [], "unclear": []}


def test_a_symptom_is_read_as_a_mention():
    read = families.read(PROBLEMS, _problems_answer(), mime="audio/webm", transcript=True)
    assert [(c.subject, c.predicate, c.value_literal) for c in read.claims] == [
        ("symptom:cough", "reported", "cough")
    ]


def test_a_symptom_said_to_have_gone_is_read_as_resolved():
    answer = _problems_answer(gone="the cough has cleared up")
    read = families.read(PROBLEMS, answer, mime="audio/webm", transcript=True)
    assert [c.predicate for c in read.claims] == ["resolved"]


def test_a_number_in_a_symptom_name_is_an_abstention_not_a_page():
    answer = _problems_answer(name="3 days of cough")
    read = families.read(PROBLEMS, answer, mime="audio/webm", transcript=True)
    assert not read.claims
    assert [a.field for a in read.abstentions] == ["symptom"]


# -- asking ------------------------------------------------------------------


def test_asking_what_symptoms_i_have_finds_them_by_when_they_were_mentioned():
    events = coughing() + [
        recording("cc3333", 12),
        mention("symptom:headache", "reported", "headache", "cc3333", 12, "headache this morning"),
    ]
    record = query.Record.of(sorted(events, key=lambda e: e.sort_key), AS_OF)
    answer = query.ask("what symptoms do I have right now", record, box="unreachable")
    assert answer.refusal is None
    lines = [passage.line for passage in answer.retrieval.passages]
    joined = "\n".join(lines)
    assert "Cough (symptom)" in joined and "Headache (symptom)" in joined
    assert "recorded 10 September 2026" in joined
    assert "not whether it is still happening" in joined
    # The words the record never says.
    assert "current" not in joined.lower()


def test_asking_what_causes_a_symptom_is_still_refused():
    record = query.Record.of(sorted(coughing(), key=lambda e: e.sort_key), AS_OF)
    answer = query.ask("what is causing my cough", record, box="unreachable")
    assert answer.refusal is not None
    assert answer.retrieval is None
