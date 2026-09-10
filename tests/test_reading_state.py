"""What happens to this next — and the line between what may be written down.

Two modules answer that question and the split between them is the thing most
worth pinning here. :mod:`agent.projection.reading` answers from the event log
and its answer is written into ``wiki/``; :mod:`agent.server.reading` adds the
reason from the extraction queue and the endpoint, and its answer is only ever
shown on a screen.

Getting that backwards breaks invariant 1. The queue is a disposable cache: a
generated file saying "waiting behind four other files" stops reproducing the
moment someone deletes ``.agent/``, and the byte-identical rebuild is what every
other guarantee in the project rests on. So there is a test that deletes the
queue and requires the wiki to come back identical, and a test that the wiki
never contains the words the live layer uses.

The rest is about not going quiet. A document that has been read, a document
waiting its turn and a recording nothing in this build will read for medications
all used to render the same way — as a line with nothing after it, which reads
as "dealt with".
"""

from __future__ import annotations

import pytest

from agent import ingest as ingest_mod
from agent.asr import runner as speech_mod
from agent.extract import jobs as jobs_mod
from agent.extract import runner as extract_runner
from agent.projection import project
from agent.projection import reading as reading_mod
from agent.server import endpoint_state
from agent.server import reading as live_mod

from .conftest import claim, confirm, ingested, on_day
from .test_speech import _speaking, _wav_bytes


def _project(vault):
    return project(list(vault.read().events), "2026-09-10T00:00:00Z")


def _readings(vault):
    projection = _project(vault)
    from agent.projection import citations

    return reading_mod.index(
        list(vault.read().events),
        citations.index_artifacts(list(vault.read().events)),
        projection.artifacts,
    )


def _row_for(vault, short: str):
    """The *artefact* row for one artefact.

    Filtered on the marker, not only the citation: a claim read off this
    artefact cites it too, and picking the first row that mentions the hash
    hands back the claim's row — which has no reading, because a claim is not
    waiting to be read.
    """
    rows = [
        row
        for row in _project(vault).rows
        if row.cite == short and row.marker == "artefact"
    ]
    assert rows, f"no artefact row cites {short}"
    return rows[0]


@pytest.fixture
def recording(vault):
    return ingest_mod.ingest_bytes(
        vault,
        _wav_bytes(),
        ingest_mod.CaptureContext(source="recorder", captured_ts="2026-09-02T09:11:00Z"),
    )


# --- from the log alone -----------------------------------------------------


def test_an_artefact_nothing_has_read_says_so(vault, recording):
    state = _readings(vault)[recording.short]

    assert state.state == reading_mod.NOT_READ
    assert state.sentence() == "Not read yet."
    assert _row_for(vault, recording.short).reading_text == "Not read yet."


def test_a_transcript_says_it_is_still_waiting_to_be_read(vault, recording):
    """Said where someone who has just spoken into their record will read it.

    Typing up happens on this machine and reading the words needs the box, so
    this state can last as long as the box is asleep. Someone who has just
    dictated a dose change would otherwise assume it is already on their
    medication list.
    """
    transcriber = speech_mod.Transcriber(
        vault, speech=_speaking("I stopped the sertraline around Easter.")
    )
    for event in transcriber.run(recording.short).events:
        vault.append(event)

    state = _readings(vault)[recording.short]

    assert state.state == reading_mod.TRANSCRIBED
    assert not state.is_finished
    assert state.sentence() == "Typed up — waiting to be read for medications."


def test_a_recording_with_no_speech_says_that_instead(vault, recording):
    """Silence is a settled answer, and promising a quotation would be a lie."""
    from .test_speech import FakeSegment, FakeSpeech

    silent = FakeSpeech([FakeSegment(0.0, 3.0, "Thank you.", no_speech_prob=0.99)])
    for event in speech_mod.Transcriber(vault, speech=silent).run(recording.short).events:
        vault.append(event)

    state = _readings(vault)[recording.short]

    assert state.state == reading_mod.TRANSCRIBED
    assert state.spoke is False
    assert state.sentence() == "No speech was found in it."
    # And the discarded hallucination is not in it, here as everywhere.
    assert "Thank you" not in _row_for(vault, recording.short).reading_text


def test_claims_from_an_artefact_count_as_having_read_it(vault):
    """Claims are stronger evidence than the bookkeeping event beside them.

    A log carrying ``claim.proposed`` without ``extraction.completed`` — a
    hand-authored one, a partial restore, the demo's own seeded stream — has
    plainly been read. Saying "not read yet" on the row directly above two
    claims cited to that artefact is a contradiction on one page.
    """
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    first = claim(device, "med:perindopril", "dose", {"amount": 5, "unit": "mg"},
                  ts=on_day(2), artifact="a3f91c")
    second = claim(device, "med:perindopril", "started", "2024-11-02",
                   ts=on_day(2, hour=11), artifact="a3f91c")
    vault.append(first)
    vault.append(second)

    state = _readings(vault)["a3f91c"]

    assert state.state == reading_mod.READ
    assert state.claims == 2
    assert state.awaiting == 2
    assert state.sentence() == "Read — 2 things added to your review list."


def test_a_decided_artefact_still_accounts_for_itself(vault):
    """A row that goes quiet cannot be told from one nothing has looked at."""
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    proposed = claim(device, "med:perindopril", "dose", {"amount": 5, "unit": "mg"},
                     ts=on_day(2), artifact="a3f91c")
    vault.append(proposed)
    vault.append(confirm(device, proposed.id, ts=on_day(3)))

    state = _readings(vault)["a3f91c"]

    assert state.state == reading_mod.READ
    assert state.awaiting == 0
    assert state.is_finished
    assert state.sentence() == "Read — 1 thing taken from it, already decided."
    assert _row_for(vault, "a3f91c").reading_text == state.sentence()


def test_the_sentence_is_terminated_once_after_a_quoted_transcript(vault, recording):
    """`changed." Typed up` — two sentences running into each other."""
    for event in speech_mod.Transcriber(
        vault, speech=_speaking("The tablets changed.")
    ).run(recording.short).events:
        vault.append(event)

    projection = _project(vault)
    page = projection.files["wiki/timeline/2026-09.md"].decode("utf-8")

    assert "changed.”." not in page
    assert "changed.” Typed up" in page


# --- the line between the two layers ----------------------------------------


def test_the_wiki_never_mentions_the_queue(vault, recording):
    """Live state in a generated file would break the rebuild the moment the
    cache is deleted, and would assert for ever that a Mac was asleep once."""
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    extract_runner.enqueue_unread(vault, queue)

    page = _project(vault).files["wiki/timeline/2026-09.md"].decode("utf-8")

    for phrase in ("asleep", "behind", "other files", "password", "queue"):
        assert phrase not in page.lower(), phrase


def test_deleting_the_queue_leaves_the_wiki_byte_identical(vault, recording):
    """Invariant 1, against the file this feature was most tempted to read."""
    from agent.projection import writer as writer_mod

    queue = jobs_mod.Queue.open(vault.root / ".agent")
    extract_runner.enqueue_unread(vault, queue)
    writer_mod.write(vault.root, _project(vault).files, "2026-09-10T00:00:00Z")
    before = {
        path.relative_to(vault.root).as_posix(): path.read_bytes()
        for path in sorted((vault.root / "wiki").rglob("*"))
        if path.is_file()
    }

    (vault.root / ".agent" / "jobs.jsonl").unlink()
    writer_mod.write(vault.root, _project(vault).files, "2026-09-10T00:00:00Z")
    after = {
        path.relative_to(vault.root).as_posix(): path.read_bytes()
        for path in sorted((vault.root / "wiki").rglob("*"))
        if path.is_file()
    }

    assert before == after


# --- the live layer ---------------------------------------------------------


def _live(vault, short, endpoint=None, mime=""):
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    return live_mod.describe(
        _readings(vault)[short],
        queue,
        endpoint or endpoint_state.EndpointState(),
        mime,
    )


def test_a_sleeping_box_is_said_on_screen_and_nowhere_else(vault):
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    asleep = endpoint_state.EndpointState(state=endpoint_state.UNREACHABLE)

    described = _live(vault, "a3f91c", asleep, "image/jpeg")

    assert described["text"] == (
        "Waiting to be read — the computer that reads your files is asleep."
    )
    # The recorded sentence stays the short one that can live in a file.
    assert described["recorded_text"] == "Not read yet."


def test_an_unconfigured_endpoint_is_not_a_sleeping_one(vault):
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    none = endpoint_state.EndpointState(state=endpoint_state.NOT_CONFIGURED)

    described = _live(vault, "a3f91c", none, "image/jpeg")

    assert described["text"] == (
        "Waiting to be read — nothing is set up to read your files yet."
    )
    assert "asleep" not in described["text"]


def test_a_rejected_key_says_a_person_has_to_act(vault):
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add("a3f91c")
    queue.park_for_auth("the key was rejected")

    described = _live(
        vault,
        "a3f91c",
        endpoint_state.EndpointState(state=endpoint_state.UNAUTHORISED),
        "image/jpeg",
    )

    assert "password" in described["text"]
    assert "refused" in described["text"]


def test_a_queued_document_says_how_many_are_ahead_of_it(vault):
    device = vault.identity.id
    for index, short in enumerate(("aaa111", "bbb222", "ccc333")):
        vault.append(ingested(device, short, ts=on_day(index + 1)))
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    for short in ("aaa111", "bbb222", "ccc333"):
        queue.add(short)
    working = endpoint_state.EndpointState(state=endpoint_state.WORKING)

    first = _live(vault, "aaa111", working, "image/jpeg")
    third = _live(vault, "ccc333", working, "image/jpeg")

    assert first["text"] == "Being read now."
    assert third["text"] == "Waiting to be read — behind 2 other files."


def test_a_recording_waiting_is_never_told_the_box_is_asleep(vault, recording):
    """It is read on this machine. A queue reason would send someone to check
    their tailnet over a transcript that is thirty seconds away."""
    asleep = endpoint_state.EndpointState(state=endpoint_state.UNREACHABLE)

    described = _live(vault, recording.short, asleep, "audio/wav")

    assert described["text"] == "Being written down on this computer."
    assert "asleep" not in described["text"]


def test_a_recording_that_could_not_be_typed_up_says_why(vault, recording):
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(recording.short)
    queue.unreadable(
        queue.for_artifact(recording.short), "faster-whisper is not installed"
    )

    described = _live(vault, recording.short, mime="audio/wav")

    assert "Could not be written down" in described["text"]
    assert "faster-whisper is not installed" in described["text"]


def test_a_transcript_is_marked_deferred_for_the_interface(vault, recording):
    for event in speech_mod.Transcriber(
        vault, speech=_speaking("I stopped the sertraline.")
    ).run(recording.short).events:
        vault.append(event)

    described = _live(vault, recording.short, mime="audio/wav")

    assert described["deferred"] is True
    assert described["finished"] is False


# --- over HTTP --------------------------------------------------------------


def test_every_artefact_row_on_the_timeline_accounts_for_itself(client, vault):
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))

    rows = client.get("/api/timeline").json()["rows"]
    artefact_rows = [row for row in rows if row["reading"] is not None]

    assert artefact_rows
    for row in artefact_rows:
        assert row["reading"]["text"]
    # And a claim row is not an artefact waiting to be read.
    assert all(row["reading"] is None for row in rows if row["marker"] != "artefact")


def test_the_artefact_page_says_what_happens_next(client, vault, recording):
    body = client.get(f"/api/artifact/{recording.short}/meta").json()

    assert body["reading"]["state"] == "not-read"
    assert body["reading"]["text"]
    assert body["reading"]["deferred"] is False
