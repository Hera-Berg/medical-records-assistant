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


# --- a supply that does not know about a reported stop ----------------------


def _reported_stop():
    """A prescriber-issued script, and a patient-reported stop the user confirmed.

    The tier rule declines to transition the status — only a prescriber-issued
    or lab-issued source takes a medication off the current list — so the entity
    stays `active` and carries a stop report beside it.
    """
    # A second artefact — a voice note, not the script — so the citation on the
    # stop is a different footnote from the citation on the supply arithmetic.
    events = _script() + [ingested(DEVICE, "77b210")]
    stop = claim(
        DEVICE, "med:perindopril", "status", "stopped", ts=on_day(20),
        occurred={"value": "2026-06-15", "precision": "month", "uncertainty_days": 10},
        tier="patient-reported", artifact="77b210",
    )
    events += [stop, confirm(DEVICE, stop.id, ts=on_day(21))]
    return _project(events)


def test_supply_acknowledges_a_reported_stop_it_cannot_act_on():
    """Both sections were right and the page was not.

    "the supply runs out 4 July 2026" sat above "you confirmed a
    patient-reported source saying this was stopped around June 2026" with
    nothing joining them, so a clinician skimming saw a live prescription and a
    stop notice that did not know about each other. Supply is arithmetic on what
    was dispensed; whether those tablets are being swallowed is exactly what the
    patient has told the record they are not.
    """
    result = _reported_stop()
    entity = result.entities["med:perindopril"]
    # `stale` here rather than `active` only because this script's supply ran
    # out before AS_OF. Both are statuses that keep the entry on the current
    # list, which is the point: the tier rule declined to take it off.
    assert entity.status == entities_mod.STALE
    assert entity.stop_report is not None

    supply = _page(result).split("## Supply")[1].split("## ")[0]

    assert "runs out 4 July 2026, if it is still being taken" in supply
    assert "you have since confirmed a patient-reported stop" in supply
    assert "Reported stopped" in supply, "and points at the section that has the detail"


def test_the_supply_caveat_appears_only_where_a_stop_was_reported():
    """The qualifier has to mean something. On a page with no reported stop it
    would be a hedge on an unhedged fact, and hedging everything is how a real
    caveat stops being read."""
    supply = _page(_project(_script())).split("## Supply")[1]

    assert "runs out 4 July 2026." in supply
    assert "if it is still being taken" not in supply
    assert "Reported stopped" not in supply


def test_the_supply_caveat_is_cited_to_the_stop_not_to_the_script():
    """A sentence about what the patient said must not carry the script's
    footnote. That is the same substitution `_supply_source_phrase` refuses when
    it declines to call a correction a prescriber-issued source."""
    page = _page(_reported_stop())
    supply = page.split("## Supply")[1].split("## ")[0]
    paragraph = [
        line for line in supply.splitlines() if "confirmed a patient-reported stop" in line
    ]
    assert paragraph, "the sentence is on the page"
    # Markers are written once per paragraph per source, so both appear on the
    # line — but the stop's must be among them, and it is a different artefact
    # from the script the arithmetic came off.
    markers = _MARKER.findall(paragraph[0])
    assert "77b210" in markers
    assert "a3f91c" in markers


def test_elapsed_phrases_are_never_ungrammatical():
    """"1 years ago" is a real reachable value, and it looks machine-generated.

    The month branch ends at 720 days and a year is counted as 365, so days
    720–729 land on a year count of exactly one. This sentence is read by a
    clinician off a printed page; a record that cannot pluralise reads as one
    nobody checked.
    """
    from datetime import date, timedelta

    from agent.projection.entities import elapsed_phrase

    start = date(2020, 1, 1)
    for days in range(0, 4000):
        phrase = elapsed_phrase(start, start + timedelta(days=days))
        assert " 1 years " not in f" {phrase} ", f"{days} days -> {phrase!r}"
        assert " 1 months " not in f" {phrase} ", f"{days} days -> {phrase!r}"
        assert " 1 days " not in f" {phrase} ", f"{days} days -> {phrase!r}"


# --- what a correction actually did, said accurately ------------------------
#
# Both of these were found the same way as everything above: by answering a
# review item in a scratch vault and reading the page that came out. The suite
# was green for both.


def _folded(value: str, correction: str | None = None, occurred=None):
    """Two scripts stating the same dose, then one act of the user's on both.

    A confirmation and a correction from the inbox each emit one event per
    claim, which is what stops the second document coming back to ask again.
    The question these tests answer is what that looks like on the page.
    """
    first = claim(
        DEVICE, "med:perindopril", "dose", value, ts=on_day(3), artifact="a3f91c",
        occurred={"value": "2026-09-03", "precision": "day", "uncertainty_days": 0},
    )
    second = claim(
        DEVICE, "med:perindopril", "dose", value, ts=on_day(4), artifact="77b210",
        occurred={"value": "2026-09-04", "precision": "day", "uncertainty_days": 0},
    )
    events = [
        ingested(DEVICE, "a3f91c", ts=on_day(1)),
        ingested(DEVICE, "77b210", ts=on_day(2)),
        first,
        second,
    ]
    for target in (first, second):
        events.append(
            correct(
                DEVICE,
                value=correction or value,
                target=target.id,
                ts=on_day(5),
                **({"occurred_at": occurred} if occurred else {}),
            )
        )
    result = _project(events)
    entity = result.entities["med:perindopril"]
    return result.files[entity.rel_path].decode("utf-8")


def test_one_correction_of_a_folded_fact_is_not_several_earlier_readings():
    """A correction is one act, however many claims it was recorded against.

    Correcting a fact two documents agree about emits one `claim.corrected` per
    claim. Rendering the siblings as history produced "5mg daily, replaced by
    your correction to 5mg daily" — a value replaced by itself, describing a
    change that did not happen — above the readings that genuinely were
    replaced.
    """
    page = _folded("5mg daily", correction="50mg daily")

    assert "replaced by your correction to 50mg daily" in page
    assert "50mg daily (prescriber-issued), replaced by" not in page

    # Both readings the correction replaced are still there, each with its own
    # artefact: a mistyped correction is undetectable once the original is gone.
    history = page.split("## Earlier readings")[1]
    assert history.count("5mg daily") == 2
    assert "[^a3f91c]" in history and "[^77b210]" in history


def test_dating_a_vague_phrase_is_not_described_as_replacing_the_value():
    """Answering "when was this?" changes the date, not the dose.

    The dateable review item is answered with a correction that restates the
    value verbatim and adds the date the user supplied. Describing that as
    "replaced by your correction to <the same words>" reports a change that did
    not happen, in the one section whose job is making real changes visible.
    """
    onset = claim(
        DEVICE, "problem:migraine", "onset", "since around Easter", ts=on_day(3),
        artifact="a3f91c", occurred=None, occurred_span="around Easter",
        artifact_ts="2026-05-02T00:00:00Z",
    )
    result = _project(
        [
            ingested(DEVICE, "a3f91c", ts=on_day(1)),
            onset,
            confirm(DEVICE, onset.id, ts=on_day(4)),
            correct(
                DEVICE,
                value="since around Easter",
                target=onset.id,
                ts=on_day(6),
                occurred_at={
                    "value": "2026-04-05",
                    "precision": "day",
                    "uncertainty_days": 14,
                },
            ),
        ]
    )
    page = result.files[result.entities["problem:migraine"].rel_path].decode("utf-8")

    assert "dated by you as around 5 April 2026 (±14 days)" in page
    assert "replaced by your correction to since around Easter" not in page

    # The phrase the source actually used survives, so a reader can see what
    # the date was an answer to. Discarding it would lose the evidence.
    assert "dated only as “around Easter”" in page


def test_a_correction_the_user_changed_their_mind_about_stays_visible():
    """Only a correction that agrees with the winner is folded away.

    An earlier correction stating a *different* value is a decision the user
    later reversed, and "what did I correct, and from what" has to stay
    answerable across both of them.
    """
    proposed = claim(
        DEVICE, "med:perindopril", "dose", "5Omcg daily", ts=on_day(3), artifact="a3f91c"
    )
    result = _project(
        [
            ingested(DEVICE, "a3f91c", ts=on_day(1)),
            proposed,
            correct(DEVICE, value="50mcg daily", target=proposed.id, ts=on_day(4)),
            correct(DEVICE, value="100mcg daily", target=proposed.id, ts=on_day(6)),
        ]
    )
    page = result.files[result.entities["med:perindopril"].rel_path].decode("utf-8")
    history = page.split("## Earlier readings")[1]

    assert "100mcg daily" in page.split("## Current")[1].split("##")[0]
    assert "50mcg daily" in history
    assert "5Omcg daily" in history
