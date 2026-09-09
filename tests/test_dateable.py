"""Temporal references the record cannot place, and what it does with them.

``CLAUDE.md``: "Unresolvable temporal references are preserved, never dropped and
never auto-resolved. 'Around Easter', 'last Christmas', 'the week before the
wedding' — the model copies the phrase verbatim and emits ``occurred_at: null``.
It never computes a date. The projection then raises a *dateable* review item
carrying a candidate computed in code where one exists — the computus is
deterministic, the year is not."

Three separable claims, tested separately:

- the phrase survives into the record and onto the page;
- no date is ever adopted from it;
- a candidate is offered where one can be computed, and the item works fine
  where one cannot.
"""

from __future__ import annotations

from datetime import date

import pytest

from agent import projection
from agent.projection import reconcile, temporal
from agent.projection.dates import FuzzyDate

from .conftest import claim, confirm, correct, ingested, on_day, reject

DEVICE = "laptop-a1b2"
AS_OF = "2026-09-30T00:00:00Z"


def _project(events):
    return projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)


def _with_span(span, *, subject="problem:migraine", predicate="onset",
               value="headaches", tier="patient-reported", artifact_ts=None,
               confirmed=True, **extra):
    events = [ingested(DEVICE, "a3f91c", mime="audio/webm", source="recorder")]
    proposed = claim(
        DEVICE, subject, predicate, value, ts=on_day(2), tier=tier,
        occurred=None, occurred_span=span, artifact_ts=artifact_ts, **extra,
    )
    events.append(proposed)
    if confirmed:
        events.append(confirm(DEVICE, proposed.id, ts=on_day(3)))
    return events, proposed


def _items(result, kind=reconcile.DATEABLE):
    return [item for item in result.review if item.kind == kind]


# --- the computus ----------------------------------------------------------


@pytest.mark.parametrize(
    "year, expected",
    [
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),
    ],
)
def test_easter_is_computed_not_looked_up(year, expected):
    assert temporal.easter(year) == expected


def test_the_computed_date_matches_the_spec_worked_example():
    """CLAUDE.md's own example resolves to 2026-04-05."""
    assert temporal.easter(2026).isoformat() == "2026-04-05"


def test_a_candidate_needs_a_reference_date_to_pick_a_year():
    """Which day Easter falls on is arithmetic; which year is a guess."""
    assert temporal.candidates("around Easter", None) == ()
    assert temporal.candidates("around Easter", date(2026, 6, 1)) != ()


def test_around_widens_the_candidate_into_a_band():
    (candidate,) = temporal.candidates("around Easter", date(2026, 6, 1))

    assert not candidate.date.is_exact, "'around' is not a day"
    assert "±14 days" in candidate.date.render()


def test_a_named_day_without_around_stays_exact():
    (candidate,) = temporal.candidates("on Christmas Day", date(2026, 12, 30))

    assert candidate.date.is_exact
    assert candidate.iso == "2026-12-25"


def test_last_resolves_backwards_from_the_reference():
    (candidate,) = temporal.candidates("last Christmas", date(2026, 3, 10))
    assert candidate.iso == "2025-12-25"


def test_an_occasion_still_ahead_offers_both_readings():
    """Said in February, "around Easter" could be last year's or this year's."""
    offered = temporal.candidates("around Easter", date(2026, 2, 1))

    assert [c.date.value.year for c in offered] == [2025, 2026]
    assert "before" in offered[0].reason and "following" in offered[1].reason


@pytest.mark.parametrize(
    "phrase",
    [
        "the week before the wedding",
        "when I was in hospital",
        "a couple of months ago",
        "after the op",
        "",
    ],
)
def test_a_phrase_with_nothing_computable_offers_nothing(phrase):
    assert temporal.candidates(phrase, date(2026, 6, 1)) == ()


# --- the review item -------------------------------------------------------


def test_a_phrase_raises_a_dateable_review_item():
    events, _ = _with_span("around Easter")
    items = _items(_project(events))

    assert len(items) == 1
    assert items[0].subject_id == "problem:migraine"
    assert "around Easter" in items[0].summary


def test_the_item_carries_a_candidate_where_one_can_be_computed():
    events, _ = _with_span("around Easter", artifact_ts="2026-06-01T00:00:00Z")
    (item,) = _items(_project(events))

    assert "5 April 2026" in item.summary
    assert "the date on the document" in item.summary, "says what it measured from"
    assert "confirm one, or enter the date yourself" in item.summary


def test_the_item_works_perfectly_well_without_a_candidate():
    """Candidates are a nicety; the item must stand on the phrase alone."""
    events, _ = _with_span("the week before the wedding")
    (item,) = _items(_project(events))

    assert "the week before the wedding" in item.summary
    assert "Enter the date if you know it" in item.summary


def test_the_item_is_medium_so_it_never_crowds_an_unreviewed_allergy():
    """Answering applies nothing; the claim's own gating already happened."""
    events, _ = _with_span("around Easter", subject="med:perindopril",
                           predicate="started", value="a while back",
                           tier="prescriber-issued")
    (item,) = _items(_project(events))

    assert item.consequence == "medium"


def test_a_claim_with_a_real_date_raises_nothing():
    events = [ingested(DEVICE, "a3f91c")]
    proposed = claim(
        DEVICE, "problem:migraine", "onset", "headaches", ts=on_day(2),
        occurred={"value": "2026-04-05", "precision": "month", "uncertainty_days": 14},
        occurred_span="around Easter",
    )
    events += [proposed, confirm(DEVICE, proposed.id, ts=on_day(3))]

    assert _items(_project(events)) == []


# --- nothing is ever adopted -----------------------------------------------


def test_no_date_is_written_into_the_record_from_a_phrase():
    events, _ = _with_span("around Easter", artifact_ts="2026-06-01T00:00:00Z")
    result = _project(events)
    entity = result.entities["problem:migraine"]

    assert entity.slots["onset"].winner.occurred_at is None
    assert entity.last_confirmed is None or entity.last_confirmed.iso != "2026-04-05"


def test_the_page_prints_the_phrase_and_not_a_resolved_date():
    """The phrase is evidence. The candidate would read as established fact."""
    events, _ = _with_span("around Easter", artifact_ts="2026-06-01T00:00:00Z")
    result = _project(events)
    page = result.files["wiki/problems/migraine.md"].decode("utf-8")

    assert "“around Easter”" in page
    assert "waiting for you to say when" in page
    assert "5 April 2026" not in page, "the candidate lives in the queue, not the page"
    assert "2026-04-05" not in page


def test_the_page_sentence_carries_a_citation_like_every_other():
    events, proposed = _with_span("around Easter")
    page = _project(events).files["wiki/problems/migraine.md"].decode("utf-8")

    lines = [ln for ln in page.split("\n") if "around Easter" in ln]
    assert lines, "the phrase reaches the page"
    for line in lines:
        assert "[^a3f91c]" in line


def test_the_phrase_fills_the_slot_a_date_would_have_occupied():
    """Otherwise a document date beside an onset reads as the onset date."""
    events, _ = _with_span("around Easter", artifact_ts="2026-06-01T00:00:00Z")
    page = _project(events).files["wiki/problems/migraine.md"].decode("utf-8")

    assert (
        "**Onset** — headaches (patient-reported, dated only as “around Easter”, "
        "document dated 1 June 2026)"
    ) in page


def test_the_queue_and_the_page_write_the_phrase_the_same_way():
    """One phrase, one rendering — the timeline/page drift again, avoided."""
    events, _ = _with_span("around Easter")
    result = _project(events)
    page = result.files["wiki/problems/migraine.md"].decode("utf-8")

    quoted = "“around Easter”"
    assert quoted in page
    assert quoted in _items(result)[0].summary


# --- rejected content never reappears through this route -------------------


def test_a_rejected_claims_phrase_is_never_printed_or_queued():
    """A rejection is a retraction; printing the phrase re-asserts the claim."""
    events = [ingested(DEVICE, "a3f91c", mime="audio/webm", source="recorder")]
    proposed = claim(
        DEVICE, "problem:alcohol-dependence", "name", "alcohol dependence",
        ts=on_day(2), tier="patient-reported", occurred=None,
        occurred_span="around Easter",
    )
    events += [proposed, reject(DEVICE, proposed.id, ts=on_day(3))]

    result = _project(events)

    assert _items(result) == []
    for blob in result.files.values():
        assert "around Easter" not in blob.decode("utf-8")
        assert "alcohol" not in blob.decode("utf-8")


def test_an_undecided_claim_is_not_asked_about_twice():
    """One question at a time: decide the claim, then date it."""
    events, _ = _with_span(
        "around Easter", subject="med:perindopril", predicate="started",
        value="a while back", tier="prescriber-issued", confirmed=False,
    )
    result = _project(events)

    assert _items(result) == []
    assert any(item.kind == "awaiting-confirmation" for item in result.review)


def test_the_question_arrives_once_the_claim_is_confirmed():
    events, _ = _with_span(
        "around Easter", subject="med:perindopril", predicate="started",
        value="a while back", tier="prescriber-issued", confirmed=True,
    )
    assert len(_items(_project(events))) == 1


# --- answering it ----------------------------------------------------------


def test_answering_is_a_correction_and_closes_the_question():
    """The user supplies the date, which becomes a claim.corrected."""
    events, proposed = _with_span("around Easter")
    events.append(
        correct(
            DEVICE,
            value="headaches",
            target=proposed.id,
            ts=on_day(6),
            occurred_at={"value": "2026-04-05", "precision": "month", "uncertainty_days": 14},
        )
    )
    result = _project(events)

    assert _items(result) == []
    entity = result.entities["problem:migraine"]
    assert entity.slots["onset"].winner.occurred_at == FuzzyDate(
        date(2026, 4, 5), "month", 14
    )


def test_the_original_phrase_stays_visible_after_it_is_answered():
    """"What did I correct, and from what" has to stay answerable."""
    events, proposed = _with_span("around Easter")
    events.append(
        correct(
            DEVICE, value="headaches", target=proposed.id, ts=on_day(6),
            occurred_at={"value": "2026-04-05", "precision": "month", "uncertainty_days": 14},
        )
    )
    page = _project(events).files["wiki/problems/migraine.md"].decode("utf-8")

    assert "Earlier readings" in page
