"""The consultation sheet: what reaches it, what never does, and what it says.

The sheet is the artefact the whole project exists to produce, and almost every
invariant upstream of it has a way of failing quietly right here — a rejected
reading reappearing in an export, an unconfirmed proposal printed as fact, a
medication dropped to make a page fit. Each of those gets a test of its own,
because each is invisible on a sheet that otherwise looks correct.

Nothing in this module calls a model, and that is not an omission: no part of a
consultation summary is model-written, permanently. See
:mod:`agent.summary`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.summary import budget, store
from agent.summary import html as html_mod
from agent.summary import markdown as md_mod
from agent.summary.model import DEMO_WARNING, UNCONFIRMED_WORD

from .conftest import claim, confirm, correct, ingested, on_day, reject

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def ordered(*events):
    """One stream in the log's own order, from events and groups of events.

    Groups are flattened, so a fact that takes a proposal *and* a confirmation
    can be written as one line in a test and still read as two events.
    """
    flat = []
    for item in events:
        flat.extend(item) if isinstance(item, (list, tuple)) else flat.append(item)
    return sorted(flat, key=lambda e: e.sort_key)


def text_of(summary) -> str:
    """Everything the sheet prints, in both formats, as one string.

    Both, always. There are two renderers and a property enforced in one of them
    is a property the other will eventually lose — which is the whole reason the
    selection lives in one place and these assertions cover both outputs.
    """
    return md_mod.render(summary).decode("utf-8") + html_mod.render(summary)


@pytest.fixture
def device(vault):
    return vault.identity.id


@pytest.fixture
def basic(device):
    """A confirmed dose, a confirmed allergy, and the documents behind them."""
    dose = claim(
        device, "med:perindopril", "dose", "5mg daily", ts=on_day(2), artifact="a3f91c"
    )
    allergy = claim(
        device, "allergy:penicillin", "reaction", "rash", ts=on_day(3), artifact="77b210"
    )
    return ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        ingested(device, "77b210", ts=on_day(1)),
        dose,
        allergy,
        confirm(device, dose.id, ts=on_day(4)),
        confirm(device, allergy.id, ts=on_day(4)),
    ), dose, allergy


# --- what the sheet is -------------------------------------------------------


def test_the_sheet_says_what_it_is_in_both_formats(basic):
    """A dateline survives into every export, for the desk it is found on.

    Someone picks this up three weeks later with no context. Both renderers take
    the wording from one constant, and this is what stops a new format shipping
    without it.
    """
    events, _, _ = basic
    summary = store.compose(events, as_of=NOW, question="Why the change?", label="gp")
    for rendered in (md_mod.render(summary).decode("utf-8"), html_mod.render(summary)):
        assert "Prepared 11 September 2026 from a patient-held record" in rendered
        assert "does not interpret" in rendered


def test_demo_warning_reaches_every_format(basic):
    """An invented sheet says so wherever it is read.

    A synthetic sheet that reads as a real clinical document is the one export
    this project could produce that would actually cause harm, so the warning is
    not conditional on which renderer happens to run.
    """
    events, _, _ = basic
    summary = store.compose(events, as_of=NOW, demo=True)
    assert DEMO_WARNING in md_mod.render(summary).decode("utf-8")
    assert DEMO_WARNING in html_mod.render(summary)

    plain = store.compose(events, as_of=NOW, demo=False)
    assert DEMO_WARNING not in text_of(plain)


def test_the_question_is_printed_verbatim(basic):
    """The one thing on the page the patient wrote, unedited."""
    events, _, _ = basic
    asked = "Is the statin still needed? I get aches in my legs and I am not sure."
    summary = store.compose(events, as_of=NOW, question=asked)
    assert summary.question == asked
    assert asked in md_mod.render(summary).decode("utf-8")
    assert asked in html_mod.render(summary)


def test_every_line_carries_a_source(basic):
    """A row with no evidence cannot be constructed, let alone printed."""
    events, _, _ = basic
    summary = store.compose(events, as_of=NOW)
    lines = [line for section in summary.sections for line in section.lines]
    assert lines
    for line in lines:
        assert line.sources
        assert line.source_text


# --- the section order the spec fixes ---------------------------------------


def test_sections_are_in_the_order_the_spec_fixes(basic):
    """What changed, medications, allergies, problems — then the question.

    Allergies is not one of the four the spec names and is on the sheet anyway:
    a handed clinical summary without allergies is not a clinical summary. It
    sits after medications, which leaves the four named sections in their given
    order.
    """
    events, _, _ = basic
    summary = store.compose(events, as_of=NOW)
    assert [s.key for s in summary.sections] == [
        "changes",
        "medications",
        "allergies",
        "problems",
    ]
    rendered = html_mod.render(summary)
    assert rendered.index("Current medications") < rendered.index("Allergies")
    assert rendered.index("Allergies") < rendered.index("Active problems")
    assert rendered.index("Active problems") < rendered.index("What I came to ask")


# --- the gate ----------------------------------------------------------------


def test_unconfirmed_high_consequence_claim_is_absent(device):
    """A proposal nobody agreed to is not on a sheet handed to a clinician.

    The consequence gate already keeps it out of the record; this asserts the
    sheet has no second path to it. "Whatever the page shows is what the user
    has agreed to."
    """
    proposed = claim(
        device, "med:warfarin", "dose", "3mg daily", ts=on_day(2), artifact="a3f91c"
    )
    events = ordered(ingested(device, "a3f91c", ts=on_day(1)), proposed)
    summary = store.compose(events, as_of=NOW)
    rendered = text_of(summary)
    assert "warfarin" not in rendered.lower()
    assert "3mg daily" not in rendered


def test_an_auto_applied_medium_claim_is_marked_not_confirmed(device):
    """In the record, so on the sheet — and labelled, not passed off as agreed.

    A medium claim applies itself after its review week. Hiding it would make
    the sheet disagree with the record; printing it unmarked would claim an
    endorsement that never happened.
    """
    onset = claim(
        device,
        "problem:migraine",
        "onset",
        "since March",
        ts=on_day(1),
        artifact="a3f91c",
        occurred={"value": "2026-03-02", "precision": "day", "uncertainty_days": 0},
    )
    events = ordered(ingested(device, "a3f91c", ts=on_day(1)), onset)
    summary = store.compose(events, as_of=NOW)
    rendered = text_of(summary)
    assert "Migraine" in rendered
    assert UNCONFIRMED_WORD in rendered


# --- rejection ---------------------------------------------------------------


def test_rejected_content_never_reaches_an_export(device, tmp_path):
    """A retraction is a retraction, "not in entity pages, not in exports".

    Checked by walking the whole export rather than by inspecting the model: the
    file is what gets handed over, and the words are what must not be in it.
    """
    bad = claim(
        device,
        "problem:alcohol-dependence",
        "name",
        "alcohol dependence",
        ts=on_day(2),
        artifact="a3f91c",
    )
    events = ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        bad,
        reject(device, bad.id, ts=on_day(3)),
    )
    summary = store.compose(events, as_of=NOW)
    rendered = text_of(summary)
    assert "alcohol" not in rendered.lower()
    assert "Rejected" not in rendered


def test_a_rejection_withdraws_a_sheet_already_prepared(vault, device):
    """The sheet cannot be un-printed; it can stop being served.

    The claims each row rested on are recorded in the event, so a rejection
    arriving afterwards is detectable. The explanation names how many entries
    went and never what they said.
    """
    reading = claim(
        device, "med:perindopril", "dose", "5mg daily", ts=on_day(2), artifact="a3f91c"
    )
    for event in ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        reading,
        confirm(device, reading.id, ts=on_day(3)),
    ):
        vault.append(event)

    prepared = store.prepare(vault, as_of=NOW, question="anything?", label="gp")
    assert store.restore(list(vault.read().events), prepared.event.id).summary is not None

    vault.append(reject(device, reading.id, ts="2026-09-12T09:00:00Z"))
    restored = store.restore(list(vault.read().events), prepared.event.id)
    assert restored.summary is None
    assert restored.is_withdrawn
    assert "5mg" not in restored.explanation
    assert "rejected" in restored.explanation


def test_a_rejection_and_a_later_confirmation_leaves_the_sheet_standing(vault, device):
    """The latest decision governs, here as everywhere else."""
    reading = claim(
        device, "med:perindopril", "dose", "5mg daily", ts=on_day(2), artifact="a3f91c"
    )
    for event in ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        reading,
        confirm(device, reading.id, ts=on_day(3)),
    ):
        vault.append(event)
    prepared = store.prepare(vault, as_of=NOW, label="gp")

    vault.append(reject(device, reading.id, ts="2026-09-12T09:00:00Z"))
    vault.append(confirm(device, reading.id, ts="2026-09-13T09:00:00Z"))
    assert store.restore(list(vault.read().events), prepared.event.id).summary is not None


def test_the_was_column_never_reprints_a_rejected_value(device):
    """A retracted reading does not come back as the thing a change replaced.

    The before-picture is built without anything since rejected, so such a
    change reads as newly added — slightly wrong about history, in the only
    direction that cannot print something the user said is not true of them.
    """
    wrong = claim(
        device, "med:perindopril", "dose", "50mg daily", ts=on_day(2), artifact="a3f91c"
    )
    right = claim(
        device, "med:perindopril", "dose", "5mg daily", ts=on_day(8), artifact="77b210"
    )
    events = ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        ingested(device, "77b210", ts=on_day(7)),
        wrong,
        confirm(device, wrong.id, ts=on_day(3)),
        right,
        confirm(device, right.id, ts=on_day(9)),
        reject(device, wrong.id, ts=on_day(10)),
    )
    summary = store.compose(events, as_of=NOW, since="2026-09-05")
    rendered = text_of(summary)
    assert "5mg daily" in rendered
    assert "50mg daily" not in rendered


# --- what changed ------------------------------------------------------------


def test_a_dose_change_says_what_it_was(device):
    events = ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        ingested(device, "77b210", ts=on_day(7)),
        _confirmed(device, "med:perindopril", "dose", "4mg daily", 2, "a3f91c"),
        _confirmed(device, "med:perindopril", "dose", "5mg daily", 8, "77b210"),
    )
    summary = store.compose(events, as_of=NOW, since="2026-09-05")
    changes = summary.section("changes")
    line = next(l for l in changes.lines if l.label == "Perindopril")
    assert line.value == "Dose 5mg daily"
    assert line.note == "was 4mg daily"


def test_nothing_changed_is_a_finding_and_is_stated(device):
    events = ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        _confirmed(device, "med:perindopril", "dose", "5mg daily", 2, "a3f91c"),
    )
    summary = store.compose(events, as_of=NOW, since="2026-09-05")
    assert summary.section("changes").lines == ()
    assert "Nothing in my record has changed" in text_of(summary)


def test_the_window_is_stated_on_the_sheet(basic):
    """A section reporting changes over an unnamed period asserts a window it
    never disclosed."""
    events, _, _ = basic
    summary = store.compose(events, as_of=NOW)
    assert "no earlier summary" in summary.section("changes").subnote
    assert summary.section("changes").subnote in text_of(summary)


def test_the_reference_is_the_previous_summary(vault, device):
    """"Since last visit" means since the last sheet, which is the last visit."""
    for event in ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        _confirmed(device, "med:perindopril", "dose", "4mg daily", 2, "a3f91c"),
    ):
        vault.append(event)
    first = store.prepare(vault, as_of=NOW, label="gp")

    later = NOW + timedelta(days=20)
    reference = store.reference_for(list(vault.read().events), later)
    assert reference.source == store.FROM_PREVIOUS
    assert reference.ts == first.event.ts


def test_a_stop_below_prescriber_tier_is_not_printed_as_stopped(device):
    """The sheet cannot say stopped while the medication list still carries it.

    The patient is the authority on what they take and the prescriber on what
    was prescribed. Both go on the sheet; neither is resolved into the other.
    """
    stop = claim(
        device,
        "med:sertraline",
        "status",
        "stopped",
        tier="patient-reported",
        ts=on_day(8),
        artifact="77b210",
    )
    events = ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        ingested(device, "77b210", ts=on_day(7)),
        _confirmed(device, "med:sertraline", "dose", "50mg daily", 2, "a3f91c"),
        stop,
        confirm(device, stop.id, ts=on_day(9)),
    )
    summary = store.compose(events, as_of=NOW, since="2026-09-05")
    medications = summary.section("medications")
    assert [line.label for line in medications.lines] == ["Sertraline"]
    assert "reported stopping" in (medications.lines[0].note or "")

    change = next(l for l in summary.section("changes").lines if l.label == "Sertraline")
    assert change.value == "I have reported stopping this"
    assert "stays on my list" in (change.note or "")


# --- conflicts ---------------------------------------------------------------


def test_two_sources_that_disagree_are_both_printed(device):
    """Never one picked, never averaged — on the sheet as in the wiki."""
    events = ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        ingested(device, "77b210", ts=on_day(1)),
        # Same document date on both, which is what leaves the slot with no
        # way to rank them and is exactly the state the sheet must render.
        _confirmed(device, "med:atorvastatin", "dose", "20mg daily", 2, "a3f91c", dated=2),
        _confirmed(device, "med:atorvastatin", "dose", "40mg daily", 3, "77b210", dated=2),
    )
    summary = store.compose(events, as_of=NOW)
    line = next(
        l for l in summary.section("medications").lines if l.label == "Atorvastatin"
    )
    assert "20mg daily" in line.value_text
    assert "40mg daily" in line.value_text
    assert line.state == "Sources disagree"
    rendered = text_of(summary)
    assert "20mg daily" in rendered and "40mg daily" in rendered


# --- what is withheld --------------------------------------------------------


def test_the_footer_names_a_high_consequence_item_being_withheld(device):
    """A count alone does not tell a clinician whether the gap matters.

    The category is named and the subject never is: a high-consequence item is
    by definition something the user has not agreed to, and naming it would
    assert on paper exactly what the gate exists to withhold.
    """
    waiting = claim(
        device, "med:warfarin", "dose", "3mg daily", ts=on_day(2), artifact="a3f91c"
    )
    events = ordered(ingested(device, "a3f91c", ts=on_day(1)), waiting)
    summary = store.compose(events, as_of=NOW)
    sentence = summary.waiting.sentence
    assert "high-consequence" in sentence
    assert "medications" in sentence
    assert "warfarin" not in sentence.lower()
    assert sentence in text_of(summary)


def test_an_empty_queue_says_so(basic):
    events, _, _ = basic
    summary = store.compose(events, as_of=NOW)
    assert summary.waiting.total == 0
    assert "Nothing in my record is waiting" in summary.waiting.sentence


# --- one page ----------------------------------------------------------------


def _many_medications(device, count: int):
    events = [ingested(device, "a3f91c", ts=on_day(1))]
    for index in range(count):
        events.extend(
            _confirmed(device, f"med:drug-{index:02d}", "dose", f"{index + 1}mg daily", 2)
        )
    return ordered(*events)


def test_a_long_sheet_is_trimmed_and_says_what_it_left_out(device):
    """Never silently. A sheet that shortens itself without saying so is worse
    than one that runs long, because the reader cannot tell which they hold."""
    events = [ingested(device, "a3f91c", ts=on_day(1))]
    for index in range(40):
        events.extend(
            _confirmed(device, f"problem:thing-{index:02d}", "name", f"Thing {index}", 2)
        )
    summary = store.compose(ordered(*events), as_of=NOW)
    problems = summary.section("problems")
    assert problems.omitted > 0
    assert len(problems.lines) < 40
    assert problems.omitted_note in text_of(summary)
    assert summary.height_mm <= budget.PAGE_MM


def test_medications_are_never_dropped_to_fit_a_page(device):
    """The one place the page limit gives way.

    A missed medication is invisible to the reader and is the failure this whole
    project exists to prevent; a second page is an annoyance. So every drug
    stays, the sheet runs long, and it says on its face that it has.
    """
    summary = store.compose(_many_medications(device, 45), as_of=NOW)
    medications = summary.section("medications")
    assert len(medications.lines) == 45
    assert medications.omitted == 0
    assert summary.overflowed
    rendered = text_of(summary)
    for index in range(45):
        assert f"Drug {index:02d}" in rendered
    assert "runs past one page" in rendered


def test_allergies_are_never_dropped_either(device):
    events = [ingested(device, "a3f91c", ts=on_day(1))]
    for index in range(30):
        events.extend(
            _confirmed(device, f"allergy:thing-{index:02d}", "reaction", "rash", 2)
        )
    summary = store.compose(ordered(*events), as_of=NOW)
    assert len(summary.section("allergies").lines) == 30
    assert summary.section("allergies").omitted == 0


def test_an_ordinary_record_fits_on_one_page(basic):
    events, _, _ = basic
    summary = store.compose(
        events, as_of=NOW, question="Should I stay on this dose?", label="gp"
    )
    assert not summary.overflowed
    assert budget.fit(summary.sections, summary.question).pages == 1


# --- stability ---------------------------------------------------------------


def test_a_prepared_sheet_still_reads_the_same_after_later_events(vault, device):
    """A correction in October does not rewrite a page handed over in June.

    The sheet is re-rendered from the log truncated at its own event with
    ``as_of`` pinned, and the projection is a pure function of those two.
    """
    reading = claim(
        device, "med:perindopril", "dose", "5mg daily", ts=on_day(2), artifact="a3f91c"
    )
    for event in ordered(
        ingested(device, "a3f91c", ts=on_day(1)),
        reading,
        confirm(device, reading.id, ts=on_day(3)),
    ):
        vault.append(event)
    prepared = store.prepare(vault, as_of=NOW, question="q", label="gp")
    before = md_mod.render(prepared.summary)

    vault.append(
        correct(
            device,
            value="10mg daily",
            target=reading.id,
            subject="med:perindopril",
            predicate="dose",
            ts="2026-10-01T09:00:00Z",
        )
    )
    restored = store.restore(list(vault.read().events), prepared.event.id)
    assert md_mod.render(restored.summary) == before
    assert "10mg daily" not in md_mod.render(restored.summary).decode("utf-8")


def test_composing_twice_gives_the_same_bytes(basic):
    events, _, _ = basic
    first = store.compose(events, as_of=NOW, question="q", label="gp")
    second = store.compose(events, as_of=NOW, question="q", label="gp")
    assert md_mod.render(first) == md_mod.render(second)
    assert html_mod.render(first) == html_mod.render(second)


# --- no interpretation -------------------------------------------------------

#: Words a sheet that "reports and cites" cannot use. Invariant 7 is a product
#: decision rather than a disclaimer, so it is checked against the output rather
#: than trusted to the templates.
FORBIDDEN = (
    "concerning",
    "worrying",
    "you should",
    "recommend",
    "suggests that",
    "likely",
    "risk of",
    "abnormal",
    "consider ",
    "trend",
)


def test_the_sheet_interprets_nothing(device):
    events = [ingested(device, "a3f91c", ts=on_day(1))]
    events.extend(_confirmed(device, "med:perindopril", "dose", "5mg daily", 2))
    events.extend(_confirmed(device, "allergy:penicillin", "reaction", "rash", 2))
    events.extend(_confirmed(device, "problem:hypertension", "name", "Hypertension", 2))
    summary = store.compose(
        ordered(*events), as_of=NOW, question="Is this dose right?", demo=True
    )
    rendered = text_of(summary).lower()
    # The patient's own question is theirs to phrase however they like; it is
    # quoted, not generated, so it is excluded from the check.
    rendered = rendered.replace("is this dose right?", "")
    for word in FORBIDDEN:
        assert word not in rendered, f"the sheet used {word!r}"


# --- helpers -----------------------------------------------------------------


def _confirmed(device, subject, predicate, value, day, artifact="a3f91c", dated=None):
    """One fact the user has agreed to: the proposal, and the tap after it.

    Returned as a pair rather than appended, so a test reads as a list of facts
    and `ordered` puts them in the log's order.

    ``dated`` is when the fact is dated, and it defaults to the day the claim was
    recorded. It matters more than it looks: reconciliation ranks on evidence
    tier and then on ``occurred_at``, so two prescriber-issued readings of one
    slot with nothing to separate them are not a supersession at all — they are
    a conflict, and the projection declines to choose. Passing the same
    ``dated`` to two readings is how a test asks for that conflict on purpose,
    and passing a later one is how it asks for a dose change.
    """
    when = on_day(day if dated is None else dated)
    proposed = claim(
        device,
        subject,
        predicate,
        value,
        ts=on_day(day),
        artifact=artifact,
        artifact_ts=when,
        occurred={"value": when[:10], "precision": "day", "uncertainty_days": 0},
    )
    return [proposed, confirm(device, proposed.id, ts=on_day(day + 1))]
