"""A voice note all the way through: audio in, claims out, in two stages.

The split is the point. Phase 6 types a recording up on this machine with the
box asleep and the laptop off the tailnet, because the waiting room is where
voice capture has to work. Reading those words for medications is the
vision-language model reading text, which needs the box — so it is a second
stage, reached through the same queue, and a recording that has been typed up but
not yet read is a normal state rather than a failure.

What is asserted here that nothing else covers:

- everything a recording says is ``patient-reported``, whatever the model says;
- the audio never goes over the wire — only the words;
- re-reading the same words is skipped, and re-transcribing to different words
  is new work;
- a recording with no speech in it proposes nothing and asks nothing.
"""

from __future__ import annotations

import json
import wave

import httpx

from . import family_answers
import pytest

from agent import ingest as ingest_mod
from agent.asr import runner as speech_mod
from agent.extract import jobs as jobs_mod
from agent.extract import propose, runner as extract_runner, transcripts
from agent.extract.runner import Extractor
from agent.llm import credentials, redaction
from agent.llm.client import Client
from agent.llm.endpoint import parse as parse_endpoint
from agent.llm.settings import AuthSettings, VlmSettings

KEY = "sk-vault-a1b2c3d4e5f6a7b8c9d0"
MODEL = "Qwen3.8-Flash-Next-oQ4e-mtp"
ENV = "HEALTH_VLM_TOKEN_TEST"
PRIVATE = ["100.94.135.1"]

SPOKEN = (
    "Right, so. I stopped the sertraline, that was around Easter time. And the "
    "doctor put the atorvastatin up to forty milligrams daily."
)


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    monkeypatch.setattr(credentials, "_from_keychain", lambda: None)
    redaction.forget_all()
    yield
    redaction.forget_all()


# --- a fake speech model, and a fake box -----------------------------------


class FakeWord:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class FakeSegment:
    def __init__(self, text, words):
        self.text, self.words = text, words
        self.start, self.end = words[0].start, words[-1].end
        self.no_speech_prob = 0.01
        self.compression_ratio = 1.4
        self.avg_logprob = -0.2
        self.id = 0
        self.seek = 0
        self.tokens = ()
        self.temperature = 0.0


class FakeInfo:
    language = "en"
    duration = 12.0


class FakeSpeech:
    def __init__(self, text: str = SPOKEN):
        self.text = text

    def transcribe(self, path, **options):
        if not self.text:
            return [], FakeInfo()
        words = [
            FakeWord(word, 0.5 + index * 0.4, 0.8 + index * 0.4)
            for index, word in enumerate(self.text.split())
        ]
        return [FakeSegment(self.text, words)], FakeInfo()


def _wav_bytes(seconds: float = 1.0, rate: int = 8000) -> bytes:
    import io
    import struct

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", 0) for _ in range(int(seconds * rate))))
    return buffer.getvalue()


def _spoken_claim(**overrides):
    base = {
        "subject_kind": "med",
        "subject_name": "Atorvastatin",
        "predicate": "dose",
        "value_literal": "forty milligrams daily",
        "evidence_tier": "patient-reported",
        "occurred_at": None,
        "occurred_span": "around Easter time",
        "dispense": None,
        "source_span": "the atorvastatin up to forty milligrams daily",
        "confidence": 0.8,
    }
    return {**base, **overrides}


def _answer(claims=None, model=MODEL, readable=True):
    payload = {
        "artifact_kind": "note",
        "readable": readable,
        "unreadable_reason": None,
        "document_date": None,
        "claims": [_spoken_claim()] if claims is None else claims,
    }
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": json.dumps(payload)}}
        ],
        "usage": {"prompt_tokens": 400},
    }


class Box:
    """A fake inference box that records exactly what it was sent."""

    def __init__(self, body=None):
        self.body = body or _answer()
        self.requests: list[dict] = []

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, json=self.body)


def _client(vault, handler):
    settings = VlmSettings(
        endpoint=parse_endpoint("https://box.tailnet.ts.net/v1"),
        model=MODEL,
        auth=AuthSettings(api_key_env=ENV),
    )
    return Client(
        settings,
        vault_root=vault.root,
        transport=httpx.MockTransport(family_answers.wrap(handler)),
        resolver=lambda h, p=None: list(PRIVATE),
    )


@pytest.fixture
def recorded(vault):
    """A recording in the vault, with its bytes actually in ``raw/``."""
    return ingest_mod.ingest_bytes(
        vault,
        _wav_bytes(),
        ingest_mod.CaptureContext(source="recorder", captured_ts="2026-09-02T09:11:00Z"),
    )


def _transcribe(vault, text: str = SPOKEN, force: bool = False):
    """Run the local speech stage, appending what it produces."""
    transcriber = speech_mod.Transcriber(vault, speech=FakeSpeech(text))
    outcome = transcriber.run(_only_recording(vault), force=force)
    for event in outcome.events:
        vault.append(event)
    return outcome


def _only_recording(vault) -> str:
    from agent.projection import citations as citations_mod

    artifacts = citations_mod.index_artifacts(list(vault.read().events))
    return next(short for short, a in artifacts.items() if a.mime.startswith("audio/"))


# --- the two stages --------------------------------------------------------


def test_a_recording_is_read_from_its_words_and_never_from_its_bytes(vault, recorded):
    """The audio does not go over the wire. The transcript does."""
    _transcribe(vault)
    box = Box()
    with _client(vault, box) as client:
        outcome = Extractor(vault, client).run(recorded.short)

    assert outcome.claims == 1
    assert len(box.requests) == 3, "medications, allergies, problems"
    sent = json.dumps(box.requests)
    assert SPOKEN in sent
    # No image parts, and nothing base64-shaped: this is a text prompt.
    assert "image_url" not in sent
    assert "data:audio" not in sent


def test_a_recording_waiting_to_be_typed_up_is_not_a_failure(vault, recorded):
    """Nothing to read yet, and the message says which command produces it."""
    box = Box()
    with _client(vault, box) as client:
        outcome = Extractor(vault, client).run(recorded.short)

    assert box.requests == [], "the box was not asked about a recording with no words"
    assert outcome.reading == extract_runner.READ_UNTRANSCRIBED
    assert "health-agent transcribe" in outcome.reason


def test_claims_from_a_recording_are_patient_reported_whatever_the_model_says(
    vault, recorded
):
    """A voice note cannot carry a prescriber's authority.

    The schema permits no other value, the validator caps by mime, and the
    reader holds the tier as well. This drives the model straight past the first
    two by answering with a tier the grammar would have refused, because the
    thing being protected is ranking: a recording that outranked a script would
    quietly beat the prescription in the wiki.
    """
    _transcribe(vault)
    box = Box(_answer([_spoken_claim(evidence_tier="prescriber-issued")]))
    with _client(vault, box) as client:
        outcome = Extractor(vault, client).run(recorded.short)

    # The voice-note grammar has no word for a prescriber's authority, so an
    # answer using one does not match it and is refused whole — nothing proposed,
    # nothing capped into shape. Validate, then reject; never repair.
    assert [e for e in outcome.events if e.type == propose.CLAIM_PROPOSED] == []
    assert outcome.reading == extract_runner.READ_REFUSED


def test_a_claim_that_got_past_the_grammar_is_still_held_at_patient_reported():
    """The cap is the second line: it holds even an answer the grammar allowed."""
    from agent.extract import families

    answer = {
        "artifact_kind": "note", "readable": True, "unreadable_reason": None,
        "document_date": None, "unclear": [],
        "medications": [{
            "name": "Atorvastatin", "strength": "forty milligrams", "frequency": "daily",
            "stopped": None, "dispense": None, "evidence_tier": "prescriber-issued",
            "occurred_at": None, "occurred_span": None, "source_span": "forty", "confidence": 0.8,
        }],
    }
    (claim,) = families.read("medications", answer, mime="audio/webm").claims
    assert claim.evidence_tier == "patient-reported"
    assert any("recording" in note for note in claim.notes)


def test_a_vague_date_stays_a_phrase(vault, recorded):
    """"Around Easter" is copied, never resolved. The year is a guess."""
    _transcribe(vault)
    with _client(vault, Box()) as client:
        outcome = Extractor(vault, client).run(recorded.short)

    (claim,) = [e for e in outcome.events if e.type == propose.CLAIM_PROPOSED]
    assert claim.payload["occurred_at"] is None
    assert claim.payload["occurred_span"] == "around Easter time"


# --- citing the seconds ----------------------------------------------------


def test_a_claim_cites_the_seconds_it_was_said_in(vault, recorded):
    _transcribe(vault)
    with _client(vault, Box()) as client:
        outcome = Extractor(vault, client).run(recorded.short)

    (claim,) = [e for e in outcome.events if e.type == propose.CLAIM_PROPOSED]
    span = claim.payload["audio_span"]
    assert span["start"] < span["end"]
    assert span["text"] == "the atorvastatin up to forty milligrams daily"


def test_a_normalised_value_carries_no_span_rather_than_a_wrong_one(vault, recorded):
    """The model tidies as it reads, the match fails, and nothing is emitted.

    The claim still cites the recording as a whole, which is true. A timestamp
    pointing at the wrong four seconds would not be.
    """
    _transcribe(vault)
    box = Box(_answer([_spoken_claim(source_span="atorvastatin 40mg daily")]))
    with _client(vault, box) as client:
        outcome = Extractor(vault, client).run(recorded.short)

    (claim,) = [e for e in outcome.events if e.type == propose.CLAIM_PROPOSED]
    assert "audio_span" not in claim.payload
    # The recording-level citation stands in its place, and it is intact.
    assert claim.provenance["artifact"] == recorded.short


# --- doing the work once ---------------------------------------------------


def test_reading_the_same_words_twice_proposes_nothing_the_second_time(vault, recorded):
    _transcribe(vault)
    box = Box()
    with _client(vault, box) as client:
        extractor = Extractor(vault, client)
        first = extractor.run(recorded.short)
        for event in first.events:
            vault.append(event)
        second = extractor.run(recorded.short)

    assert first.claims == 1
    assert second.reading == extract_runner.READ_ALREADY
    assert second.events == ()
    assert len(box.requests) == 3, "one reading's three turns, and nothing the second time"


def test_re_transcribing_to_different_words_is_new_work(vault, recorded):
    """A better speech model changes what there is to read, so it is read again.

    This falls out of the transcript being part of the rendered prompt rather
    than out of a rule about transcripts: the prompt hash *is* the identity of
    the question, and different words are a different question.
    """
    _transcribe(vault)
    box = Box()
    with _client(vault, box) as client:
        extractor = Extractor(vault, client)
        for event in extractor.run(recorded.short).events:
            vault.append(event)

        _transcribe(vault, SPOKEN.replace("forty", "eighty"), force=True)
        again = extractor.run(recorded.short)

    assert again.reading == extract_runner.READ_CLAIMS
    assert len(box.requests) == 6
    assert "eighty milligrams" in json.dumps(box.requests[3])


# --- silence ---------------------------------------------------------------


def test_a_silent_recording_asks_the_model_nothing(vault, recorded):
    """Forty seconds of a cough. Expected output: nothing at all."""
    _transcribe(vault, text="")
    box = Box()
    with _client(vault, box) as client:
        outcome = Extractor(vault, client).run(recorded.short)

    assert box.requests == []
    assert outcome.events == ()
    assert outcome.reading == extract_runner.READ_NOTHING
    assert "no speech" in outcome.reason


# --- through the queue -----------------------------------------------------


def test_the_two_drains_hand_a_recording_between_them(vault, recorded):
    """One queue, two readers, and the handover is a queued job.

    The speech drain finishes its own job and queues the recording again for the
    reader that needs the box. That is what makes a voice note captured on a
    plane become claims when the laptop is back on the tailnet, with no command
    to remember.
    """
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    extract_runner.enqueue_unread(vault, queue)

    speech_mod.drain(vault, speech_mod.Transcriber(vault, speech=FakeSpeech()), queue)
    assert queue.for_artifact(recorded.short).state == jobs_mod.QUEUED

    box = Box()
    with _client(vault, box) as client:
        report = extract_runner.drain(vault, Extractor(vault, client), queue)

    assert report.stats()["claims"] == 1
    assert queue.for_artifact(recorded.short).state == jobs_mod.DONE
    proposed = [e for e in vault.read().events if e.type == propose.CLAIM_PROPOSED]
    assert [e.payload["subject"] for e in proposed] == ["med:atorvastatin"]


def test_an_untranscribed_recording_is_left_for_the_other_drain(vault, recorded):
    """Passed over silently, and the job is not marked: it is not this reader's."""
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    extract_runner.enqueue_unread(vault, queue)

    box = Box()
    with _client(vault, box) as client:
        report = extract_runner.drain(vault, Extractor(vault, client), queue)

    assert box.requests == []
    assert report.outcomes == ()
    assert queue.for_artifact(recorded.short).state == jobs_mod.QUEUED
    assert "health-agent transcribe" in (report.idle_reason or "")
