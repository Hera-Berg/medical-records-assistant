"""The speech path: what it keeps, what it refuses to hear, and what it never does.

Most of these run against a **stub** speech model rather than ``faster-whisper``.
That is deliberate and it is not a shortcut: what has to be true of this path is
that a hallucinated segment cannot reach the record, that the original audio is
never touched, that a retry appends nothing twice, and that none of it needs the
box. None of those are claims about how well Whisper hears — they are claims
about the code around it, and a stub is how they get asserted without a 500 MB
download in the test suite.

The claims that *are* about the model are scored by ``health-agent eval
--speech`` against real synthesised audio, which is where they belong: that
needs a model on the machine, and nothing in the ordinary suite may.
"""

from __future__ import annotations


import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent import ingest as ingest_mod
from agent.asr import audio as audio_mod
from agent.asr import hotwords as hotwords_mod
from agent.asr import runner as speech_mod
from agent.asr import transcribe as transcribe_mod
from agent.extract import jobs as jobs_mod
from agent.extract import runner as extract_runner
from agent.projection import project

from .conftest import claim, confirm, on_day

# --- a stub speech model -----------------------------------------------------


@dataclass
class FakeWord:
    start: float
    end: float
    word: str
    probability: float = 0.9


@dataclass
class FakeSegment:
    start: float
    end: float
    text: str
    no_speech_prob: float = 0.01
    avg_logprob: float = -0.2
    compression_ratio: float = 1.4
    words: tuple = ()


@dataclass
class FakeInfo:
    language: str = "en"
    duration: float = 12.0


class FakeSpeech:
    """Returns fixed segments, and records what it was asked."""

    def __init__(self, segments):
        self.segments = segments
        self.calls: list[dict] = []

    def transcribe(self, path, **options):
        self.calls.append({"path": Path(path), **options})
        return list(self.segments), FakeInfo()


def _speaking(text: str = "I stopped the sertraline around Easter.") -> FakeSpeech:
    words = tuple(
        FakeWord(start=0.5 + index * 0.4, end=0.8 + index * 0.4, word=word)
        for index, word in enumerate(text.split())
    )
    return FakeSpeech([FakeSegment(start=0.5, end=6.0, text=text, words=words)])


def _wav_bytes(seconds: float = 1.0, rate: int = 8000) -> bytes:
    """A real, decodable WAV. ffmpeg has to be able to open it."""
    import io
    import struct

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(struct.pack("<h", 0) for _ in range(int(seconds * rate)))
        )
    return buffer.getvalue()


@pytest.fixture
def recorded(vault):
    """A vault holding one recording, with its bytes actually in ``raw/``."""
    result = ingest_mod.ingest_bytes(
        vault,
        _wav_bytes(),
        ingest_mod.CaptureContext(source="recorder", captured_ts="2026-09-02T09:11:00Z"),
    )
    return result


needs_ffmpeg = pytest.mark.skipif(
    audio_mod.ffmpeg_path() is None, reason="ffmpeg is not installed"
)


# --- what must never reach the record ---------------------------------------


def test_a_segment_the_model_calls_silence_is_dropped(recorded, vault):
    """Whisper's subtitle artefacts over silence, refused by its own estimate."""
    speech = FakeSpeech(
        [FakeSegment(start=0.0, end=3.0, text="Thank you.", no_speech_prob=0.98)]
    )
    path = vault.raw.resolve_recorded(recorded.rel)

    transcript = transcribe_mod.transcribe(path, speech=speech)

    assert transcript.text == ""
    assert [item.reason for item in transcript.dropped] == [transcribe_mod.DROPPED_SILENCE]
    # Kept in the transcript object as provenance — but not in `text`, which is
    # the only thing anything derived ever reads.
    assert transcript.dropped[0].text == "Thank you."


@pytest.mark.parametrize(
    "segment,reason",
    [
        (
            FakeSegment(0.0, 3.0, "la la la la la la la", compression_ratio=9.9),
            transcribe_mod.DROPPED_RATIO,
        ),
        (
            FakeSegment(0.0, 3.0, "possibly a word", avg_logprob=-2.5),
            transcribe_mod.DROPPED_LOGPROB,
        ),
        (
            FakeSegment(0.0, 3.0, "Subtitles by the Amara.org community"),
            transcribe_mod.DROPPED_ARTEFACT,
        ),
    ],
)
def test_every_filter_refuses_its_own_kind_of_invention(recorded, vault, segment, reason):
    path = vault.raw.resolve_recorded(recorded.rel)

    transcript = transcribe_mod.transcribe(path, speech=FakeSpeech([segment]))

    assert transcript.text == ""
    assert [item.reason for item in transcript.dropped] == [reason]


def test_the_artefact_phrase_list_only_matches_a_whole_segment(recorded, vault):
    """A phrase list is the crudest filter here; it must not touch real speech."""
    said = "Thank you for watching the nurse do the blood pressure cuff."
    path = vault.raw.resolve_recorded(recorded.rel)

    transcript = transcribe_mod.transcribe(path, speech=_speaking(said))

    assert transcript.text == said
    assert transcript.dropped == ()


def test_a_dropped_segment_reaches_no_generated_file(vault, recorded):
    """The end-to-end version: it is in the log, and in nothing derived."""
    speech = FakeSpeech(
        [
            FakeSegment(0.0, 3.0, "Thank you for watching.", no_speech_prob=0.99),
            FakeSegment(3.0, 8.0, "The headaches have been better.", words=()),
        ]
    )
    transcriber = speech_mod.Transcriber(vault, speech=speech)

    outcome = transcriber.run(recorded.short)
    for event in outcome.events:
        vault.append(event)

    projection = project(list(vault.read().events), "2026-09-10T00:00:00Z")
    rendered = b"\n".join(projection.files.values()).decode("utf-8")
    rows = " ".join(row.text for row in projection.rows)

    assert "Thank you for watching" not in rendered
    assert "Thank you for watching" not in rows
    assert "The headaches have been better." in rows
    # And it *is* in the log, because provenance is the reason it was kept.
    log_text = "".join(
        path.read_text(encoding="utf-8") for path in vault.events_dir.iterdir()
    )
    assert "Thank you for watching" in log_text


# --- the recording itself ----------------------------------------------------


@needs_ffmpeg
def test_the_working_copy_is_16k_mono_outside_the_vault_and_deleted(vault, recorded):
    path = vault.raw.resolve_recorded(recorded.rel)
    seen: dict = {}

    with audio_mod.working_copy(path) as copy:
        seen["path"] = copy.path
        with wave.open(str(copy.path), "rb") as handle:
            seen["rate"] = handle.getframerate()
            seen["channels"] = handle.getnchannels()

    assert seen["rate"] == audio_mod.SAMPLE_RATE
    assert seen["channels"] == audio_mod.CHANNELS
    assert vault.root not in seen["path"].parents
    assert not seen["path"].exists()
    assert not seen["path"].parent.exists()


@needs_ffmpeg
def test_the_working_copy_is_deleted_even_when_the_model_raises(vault, recorded):
    path = vault.raw.resolve_recorded(recorded.rel)

    class Exploding:
        seen: Path | None = None

        def transcribe(self, path, **options):
            Exploding.seen = Path(path)
            raise RuntimeError("the model fell over")

    transcript = transcribe_mod.transcribe(path, speech=Exploding())

    assert not transcript.is_available
    assert "could not be transcribed" in transcript.unavailable
    assert Exploding.seen is not None and not Exploding.seen.exists()


def test_the_original_recording_is_never_touched(vault, recorded):
    path = vault.raw.resolve_recorded(recorded.rel)
    before = path.read_bytes()
    mode = path.stat().st_mode

    transcriber = speech_mod.Transcriber(vault, speech=_speaking())
    outcome = transcriber.run(recorded.short)
    for event in outcome.events:
        vault.append(event)

    assert path.exists()
    assert path.read_bytes() == before
    assert path.stat().st_mode == mode


# --- word timestamps ---------------------------------------------------------


def test_word_timestamps_survive_into_the_event(vault, recorded):
    transcriber = speech_mod.Transcriber(vault, speech=_speaking("stopped the sertraline"))

    outcome = transcriber.run(recorded.short)
    payload = outcome.events[0].payload
    words = payload["segments"][0]["words"]

    assert [word["word"] for word in words] == ["stopped", "the", "sertraline"]
    assert words[2]["start"] == pytest.approx(1.3, abs=0.01)
    # A claim can cite four seconds of a recording only if this is here.
    assert words[2]["end"] > words[2]["start"]


# --- idempotency and re-transcription ---------------------------------------


def test_a_second_run_appends_nothing(vault, recorded):
    transcriber = speech_mod.Transcriber(vault, speech=_speaking())
    first = transcriber.run(recorded.short)
    for event in first.events:
        vault.append(event)

    second = transcriber.run(recorded.short)

    assert first.events
    assert second.events == ()
    assert second.reading == speech_mod.READ_ALREADY
    assert "--force" in second.reason


def test_the_hotword_list_is_not_part_of_the_key(vault, recorded):
    """Otherwise every new medication re-transcribes the whole vault, for ever."""
    transcriber = speech_mod.Transcriber(vault, speech=_speaking())
    for event in transcriber.run(recorded.short).events:
        vault.append(event)

    device = vault.identity.id
    proposed = claim(
        device, "med:atorvastatin", "dose", {"amount": 40, "unit": "mg"},
        ts=on_day(3), artifact=recorded.short,
    )
    vault.append(proposed)
    vault.append(confirm(device, proposed.id, ts=on_day(4)))

    after = speech_mod.Transcriber(vault, speech=_speaking()).run(recorded.short)

    assert "Atorvastatin" in speech_mod.Transcriber(vault).hotwords()
    assert after.events == ()
    assert after.reading == speech_mod.READ_ALREADY


def test_force_re_reads_and_records_what_it_superseded(vault, recorded):
    """The escape hatch for the trade-off above, and it keeps both reads."""
    first = speech_mod.Transcriber(vault, speech=_speaking("the sertraline")).run(
        recorded.short
    )
    for event in first.events:
        vault.append(event)

    second = speech_mod.Transcriber(
        vault, speech=_speaking("the sertraline, forty milligrams")
    ).run(recorded.short, force=True)
    for event in second.events:
        vault.append(event)

    assert second.events
    payload = second.events[0].payload
    assert payload["supersedes"] == first.events[0].id
    assert payload["forced"] is True
    # Both are in the log; the newer one is what the record shows.
    events = list(vault.read().events)
    transcripts = [
        event.payload["transcript"]
        for event in events
        if event.type == "extraction.completed"
    ]
    assert len(transcripts) == 2
    from agent.projection.timeline import transcripts as latest

    assert latest(events)[recorded.short] == "the sertraline, forty milligrams"


# --- hotwords ----------------------------------------------------------------


def test_hotwords_are_capped_and_ordered_by_kind_then_recency(vault):
    from agent.projection.dates import FuzzyDate
    from agent.projection.entities import Entity
    from agent.projection.subjects import parse
    from datetime import date

    def entity(subject_id: str, name: str, confirmed: str | None):
        return Entity(
            subject=parse(subject_id),
            name=name,
            status="active",
            last_confirmed=(
                FuzzyDate(date.fromisoformat(confirmed)) if confirmed else None
            ),
        )

    entities = {
        "person:dr-nguyen": entity("person:dr-nguyen", "Dr Nguyen", "2026-08-01"),
        "med:older": entity("med:older", "Ramipril", "2024-01-01"),
        "med:newer": entity("med:newer", "Atorvastatin", "2026-06-04"),
        "med:undated": entity("med:undated", "Perindopril", None),
        "problem:hypertension": entity("problem:hypertension", "Hypertension", "2025-01-01"),
        "med:ok": entity("med:ok", "Flu", "2026-09-01"),
    }

    terms = hotwords_mod.collect(entities)

    # Medications first, most recently confirmed first, undated last within the
    # kind; then problems, then people. "Flu" is too short to bias on.
    assert terms == (
        "Atorvastatin",
        "Ramipril",
        "Perindopril",
        "Hypertension",
        "Dr Nguyen",
    )
    assert hotwords_mod.collect(entities, limit=2) == ("Atorvastatin", "Ramipril")


def test_a_merged_stub_is_not_biased_on(vault):
    from agent.projection.entities import Entity
    from agent.projection.subjects import parse

    entities = {
        "med:panadol": Entity(
            subject=parse("med:panadol"),
            name="Panadol",
            status="active",
            merged_into="med:paracetamol",
        ),
        "med:paracetamol": Entity(
            subject=parse("med:paracetamol"), name="Paracetamol", status="active"
        ),
    }

    assert hotwords_mod.collect(entities) == ("Paracetamol",)


def test_the_hotwords_actually_reach_the_model(vault, recorded):
    speech = _speaking()
    device = vault.identity.id
    proposed = claim(
        device, "med:perindopril", "dose", {"amount": 5, "unit": "mg"},
        ts=on_day(3), artifact=recorded.short,
    )
    vault.append(proposed)
    vault.append(confirm(device, proposed.id, ts=on_day(4)))

    speech_mod.Transcriber(vault, speech=speech).run(recorded.short)

    assert "Perindopril" in speech.calls[0]["hotwords"]
    assert speech.calls[0]["language"] == "en"
    assert speech.calls[0]["vad_filter"] is True
    assert speech.calls[0]["word_timestamps"] is True


# --- the queue ---------------------------------------------------------------


def test_the_vision_drain_leaves_recordings_for_the_speech_drain(vault, recorded):
    """One queue, two readers. A recording must not go terminal on the wrong one."""
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    extract_runner.enqueue_unread(vault, queue)

    class NeverCalled:
        settings = None

        def run(self, short):  # pragma: no cover - the point is it is not called
            raise AssertionError(f"the vision reader was handed {short}")

    report = extract_runner.drain(vault, NeverCalled(), queue)

    assert report.outcomes == ()
    job = queue.for_artifact(recorded.short)
    assert job is not None and job.is_runnable


def test_the_speech_drain_types_up_what_is_waiting(vault, recorded):
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    extract_runner.enqueue_unread(vault, queue)
    transcriber = speech_mod.Transcriber(vault, speech=_speaking())

    report = speech_mod.drain(vault, transcriber, queue)

    assert report.appended == 1
    assert [outcome.reading for outcome in report.outcomes] == [
        speech_mod.READ_TRANSCRIBED
    ]
    # Typed up, and back on the queue for the reader that proposes claims from
    # the words. A recording passes through both drains: the local one turns it
    # into text with the box asleep, and the reading one needs the box, so the
    # handover is a queued job rather than a call from inside this drain.
    queued = jobs_mod.Queue.open(vault.root / ".agent").for_artifact(recorded.short)
    assert queued.state == jobs_mod.QUEUED


def test_a_silent_recording_is_not_handed_on_to_be_read(vault, recorded):
    """Nothing was said, so there is nothing for the reader to read.

    Re-queueing it would put a job on the queue that can only ever come back
    "nothing found" — inbox debt moved into the work queue, where the user
    cannot even see it to clear it.
    """
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    extract_runner.enqueue_unread(vault, queue)
    transcriber = speech_mod.Transcriber(vault, speech=FakeSpeech([]))

    report = speech_mod.drain(vault, transcriber, queue)

    assert [outcome.reading for outcome in report.outcomes] == [speech_mod.READ_NO_SPEECH]
    assert jobs_mod.Queue.open(vault.root / ".agent").for_artifact(
        recorded.short
    ).state == jobs_mod.DONE


def test_a_missing_speech_model_marks_the_job_and_keeps_the_audio(vault, recorded):
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    extract_runner.enqueue_unread(vault, queue)

    class Absent:
        def transcribe(self, path, **options):
            raise audio_mod.DecodeUnavailable("faster-whisper is not installed")

    report = speech_mod.drain(
        vault, speech_mod.Transcriber(vault, speech=Absent()), queue
    )

    assert report.appended == 0
    assert report.outcomes[0].reading == speech_mod.READ_UNAVAILABLE
    assert vault.raw.resolve_recorded(recorded.rel).exists()
    reopened = jobs_mod.Queue.open(vault.root / ".agent").for_artifact(recorded.short)
    assert reopened.state == jobs_mod.UNREADABLE
    assert "faster-whisper" in reopened.reason


# --- the transcript in the record -------------------------------------------


def test_the_timeline_says_what_the_recording_said(vault, recorded):
    transcriber = speech_mod.Transcriber(
        vault, speech=_speaking("The headaches have been better since the tablets changed.")
    )
    for event in transcriber.run(recorded.short).events:
        vault.append(event)

    projection = project(list(vault.read().events), "2026-09-10T00:00:00Z")
    rows = [row for row in projection.rows if row.cite == recorded.short]

    assert rows
    assert "The headaches have been better" in rows[0].text
    assert "was added to the record" not in rows[0].text


def test_an_untranscribed_recording_still_has_a_row(vault, recorded):
    """Nothing waits on the transcript to appear in the record."""
    projection = project(list(vault.read().events), "2026-09-10T00:00:00Z")
    rows = [row for row in projection.rows if row.cite == recorded.short]

    assert rows
    assert "was added to the record" in rows[0].text


def test_a_transcript_does_not_break_byte_identical_rebuild(vault, recorded):
    from agent.projection import writer as writer_mod

    transcriber = speech_mod.Transcriber(vault, speech=_speaking())
    for event in transcriber.run(recorded.short).events:
        vault.append(event)

    first = project(list(vault.read().events), "2026-09-10T00:00:00Z")
    writer_mod.write(vault.root, first.files, "2026-09-10T00:00:00Z")
    before = {
        path.relative_to(vault.root).as_posix(): path.read_bytes()
        for path in sorted((vault.root / "wiki").rglob("*"))
        if path.is_file()
    }

    second = project(list(vault.read().events), "2026-09-10T00:00:00Z")
    writer_mod.write(vault.root, second.files, "2026-09-10T00:00:00Z")
    after = {
        path.relative_to(vault.root).as_posix(): path.read_bytes()
        for path in sorted((vault.root / "wiki").rglob("*"))
        if path.is_file()
    }

    assert before == after
