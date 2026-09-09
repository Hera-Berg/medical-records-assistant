"""What a page says to someone skimming it, as opposed to what it asserts.

Every defect here was found by rendering the demo vault and reading the files by
hand, with the whole suite passing. They are not about any single value being
wrong — each value on each of these pages was correct. They are about how the
page reads as a whole: a stopped medication whose supply section counts down to
a future date, a paragraph that prints the same footnote three times, a null
that asserts emptiness, and a date in the body that a reader compares against a
different one in the footnote and concludes the page contradicts itself.

Assertions catch the propositions someone thought to write down. These are the
ones that only turned up on sight, written down afterwards so they stay caught.
"""

from __future__ import annotations

import re

import pytest

from agent import projection
from agent.projection import entities as entities_mod
from .conftest import claim, confirm, correct, ingested, on_day

DEVICE = "elwood-laptop"

AS_OF = "2026-09-30T00:00:00Z"

#: A footnote reference, as opposed to a footnote definition at the line start.
_MARKER = re.compile(r"\[\^([^\]]+)\](?!:)")


def _project(events):
    return projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)


#: A script that states its own date, so both the body and the footnote have an
#: `artifact_ts` to name. The two are only comparable when both are present.
SCRIPT_DATE = "2026-06-04T00:00:00Z"

FULL_DISPENSE = {
    "quantity": "30 tablets",
    "frequency": "one daily",
    "repeats": "no repeats",
}


def _script(dispense=FULL_DISPENSE):
    """One confirmed prescriber-issued dose claim off a dated script."""
    events = [ingested(DEVICE, "a3f91c", artifact_ts=SCRIPT_DATE)]
    dose = claim(
        DEVICE,
        "med:perindopril",
        "dose",
        "5mg daily",
        ts=on_day(2),
        occurred={"value": "2026-06-04"},
        artifact_ts=SCRIPT_DATE,
        dispense=dispense,
    )
    events += [dose, confirm(DEVICE, dose.id, ts=on_day(3))]
    return events


def _page(result, subject_id="med:perindopril"):
    return result.files[result.entities[subject_id].rel_path].decode("utf-8")


def _frontmatter(page: str) -> str:
    return page.split("---")[1]


# --- a stopped medication must not advertise a supply ----------------------


def _stopped():
    events = _script()
    stop = claim(
        DEVICE, "med:perindopril", "status", "stopped", ts=on_day(4),
        occurred={"value": "2026-06-20"}, artifact_ts="2026-06-20T00:00:00Z",
    )
    events += [stop, confirm(DEVICE, stop.id, ts=on_day(5))]
    return _project(events)


def test_a_stopped_medication_projects_no_exhaustion_date():
    """The date is a countdown, and there is nothing left to count down.

    `expected_exhaustion: 2026-07-04` under `status: stopped` is read by a
    clinician skimming the page as a live supply. Both this and `stale` are
    projections forward from a script and neither survives the stop.
    """
    result = _stopped()
    entity = result.entities["med:perindopril"]

    assert entity.status == entities_mod.STOPPED
    assert entity.expected_exhaustion is None
    assert entity.stale is False
    assert "expected_exhaustion" not in _frontmatter(_page(result))


def test_a_stopped_medications_supply_is_stated_in_the_past():
    """Kept, because rule 4 keeps a stopped medication's full history.

    The spans the script recorded are real record content and nothing is ever
    removed. They just stop being a claim about what is in the cupboard.
    """
    page = _page(_stopped())
    supply = page.split("## Supply")[1]

    assert "was a document dated 4 June 2026" in supply
    assert "It recorded 30 tablets" in supply
    assert "which was 30 days of supply" in supply
    assert "runs out" not in supply


def test_a_supply_section_needs_a_supply_to_report():
    """A dose's own frequency reaches Dispense as a fallback, and is not supply.

    Without it a conflicted entity rendered a Supply heading whose only sentence
    was that no exhaustion date could be recorded — a section that exists to say
    it has nothing to say, on the page of a medication that most needs reading.
    """
    events = [ingested(DEVICE, "a3f91c")]
    dose = claim(
        DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(2),
        occurred={"value": "2026-06-04"},
    )
    events += [dose, confirm(DEVICE, dose.id, ts=on_day(3))]
    result = _project(events)

    entity = result.entities["med:perindopril"]
    assert entity.dispense is not None, "the frequency fallback still parses"
    assert entity.dispense.has_spans is False
    assert "## Supply" not in _page(result)


def test_an_unreadable_quantity_still_says_why_there_is_no_date():
    """The narrowing must not swallow the case the section is actually for."""
    result = _project(
        _script(dispense={"quantity": "a shoebox full", "frequency": "one daily"})
    )
    page = _page(result)

    assert "## Supply" in page
    assert "no expected exhaustion date is recorded here because" in page.lower()


# --- one marker per paragraph ---------------------------------------------


def test_a_paragraph_on_one_source_is_cited_once():
    """Three identical markers train a reader to ignore all of them.

    The rule is that every claim is attributable, not that every full stop
    carries a marker.
    """
    page = _page(_project(_script()))
    (line,) = [ln for ln in page.split("\n") if ln.startswith("The most recent")]

    assert "It records 30 tablets" in line, "one paragraph, several sentences"
    assert "the supply runs out" in line
    assert len(_MARKER.findall(line)) == 1
    assert line.endswith("[^a3f91c]"), "and the one marker closes the paragraph"


@pytest.mark.parametrize("events", [_script, _stopped])
def test_no_rendered_line_ever_repeats_a_footnote(events):
    result = events() if events is _stopped else _project(events())
    for rel, blob in result.files.items():
        for number, line in enumerate(blob.decode("utf-8").split("\n"), start=1):
            if line.startswith("[^"):
                continue
            keys = _MARKER.findall(line)
            assert len(keys) == len(set(keys)), f"{rel}:{number} repeats a footnote"


def test_markers_stay_per_sentence_where_the_sources_differ():
    """The collapse is only legitimate when there is one source to collapse to.

    Two prescriber-issued scripts disagreeing renders a paragraph naming both,
    and each marker has to survive — the whole point of the section is that the
    reader can tell which reading came from which document.
    """
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    for short, dose, day in (("a3f91c", "20mg daily", 2), ("77b210", "40mg daily", 3)):
        proposed = claim(
            DEVICE, "med:atorvastatin", "dose", dose, ts=on_day(day), artifact=short,
            occurred={"value": "2026-06-04"},
        )
        events += [proposed, confirm(DEVICE, proposed.id, ts=on_day(day, hour=12))]

    page = _page(_project(events), "med:atorvastatin")
    (line,) = [ln for ln in page.split("\n") if "disagree about the dose" in ln]
    assert sorted(_MARKER.findall(line)) == ["77b210", "a3f91c"]


# --- absent keys are omitted ----------------------------------------------


def test_an_unknown_frontmatter_value_is_omitted_not_written_as_null():
    """`started: null` asserts nothing that leaving it out does not.

    The event log's rule is the opposite and stays the opposite: there, an
    explicit null separates "considered and unknown" from "never asked", which
    is what stops one timestamp being filled in from another.
    """
    front = _frontmatter(_page(_project(_script())))

    assert "started" not in front
    assert "null" not in front
    assert "last_confirmed: 2026-06-04" in front, "known keys are unaffected"


def test_the_event_log_still_writes_unknown_timestamps_as_explicit_null():
    """The omission above is a rule about derived files only."""
    events = _script()
    proposed = next(e for e in events if e.type == "claim.proposed")

    assert proposed.payload["captured_ts"] is None
    assert "captured_ts" in proposed.payload


# --- every date in the body says which timestamp it is ---------------------


def test_a_body_date_names_the_timestamp_it_came_from():
    """Otherwise the page reads as contradicting its own footnote.

    "5 August 2026" in the body against "photographed 7 August 2026" in the
    footnote is `artifact_ts` against `captured_ts`: both correct, both
    unlabelled, and a reader has no way to discover that.
    """
    result = _project(_script())
    page = _page(result)

    assert "(prescriber-issued, document dated 4 June 2026)" in page
    assert "is a document dated 4 June 2026" in page
    # The footnote uses the same words for the same timestamp.
    assert "document dated" in page.split("[^a3f91c]:")[1]


def test_a_date_band_survives_the_document_date():
    """The band is uncertainty the value does not carry, so it is never dropped.

    An `occurred_at` is only suppressed where it is an exact day the document
    date already states.
    """
    events = [ingested(DEVICE, "a3f91c")]
    started = claim(
        DEVICE, "med:perindopril", "started", "November 2024", ts=on_day(2),
        occurred={"value": "2024-11-02", "precision": "month", "uncertainty_days": 15},
        artifact_ts="2026-06-04T00:00:00Z",
    )
    events += [started, confirm(DEVICE, started.id, ts=on_day(3))]

    page = _page(_project(events))
    assert (
        "November 2024 (prescriber-issued, around November 2024 (±15 days), "
        "document dated 4 June 2026)"
    ) in page


def test_a_correction_is_never_called_a_prescriber_issued_source():
    """It carries the tier so it can outrank a re-extraction. It is not a source.

    A correction inherits its target's evidence tier, which is what lets it beat
    a later model reading. Printing that tier as though a prescriber had written
    the line attributes the user's own typing to their doctor.
    """
    events = _script()
    proposed = next(e for e in events if e.type == "claim.proposed")
    events.append(
        correct(
            DEVICE,
            value="10mg daily",
            target=proposed.id,
            ts=on_day(6),
            dispense={
                "quantity": "60 tablets",
                "frequency": "one daily",
                "repeats": "no repeats",
            },
            evidence_tier="prescriber-issued",
            occurred_at={"value": "2026-06-04"},
        )
    )
    supply = _page(_project(events)).split("## Supply")[1]

    assert supply.lstrip().startswith("Your correction records 60 tablets")
    assert "prescriber-issued source" not in supply


def test_the_timeline_and_the_page_describe_one_event_the_same_way():
    """A `claim.corrected` was "Your correction" on one page and a note on another."""
    events = _script()
    proposed = next(e for e in events if e.type == "claim.proposed")
    events.append(
        correct(
            DEVICE, value="10mg daily", target=proposed.id, ts=on_day(6),
            evidence_tier="prescriber-issued", occurred_at={"value": "2026-06-04"},
        )
    )
    result = _project(events)
    timeline = result.files["wiki/timeline/2026-06.md"].decode("utf-8")

    assert "Your correction" in timeline
    assert "Note recorded" not in timeline
