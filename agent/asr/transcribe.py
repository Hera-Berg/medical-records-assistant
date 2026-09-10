"""Reading a recording, and refusing to hear things that were not said.

The only module in this project that imports a speech SDK. ``CLAUDE.md`` puts
both models behind ``agent/llm/`` and ``agent/asr/`` and forbids a
model-specific import anywhere else, so a change of speech library is a change
to this file and nothing else.

**The hard part is silence, not speech.** Whisper's training corpus is subtitles,
and over silence it produces subtitle-shaped text: "Thank you for watching",
"Subtitles by …", a run of full stops. That output is fluent, confident and
completely invented, and this record's whole failure mode is fluent invention. A
fixture in the corpus is forty seconds of silence and a cough, and its expected
output is **nothing at all**.

So five filters run, in order of how much they can be trusted:

1. **VAD**, which removes silence before the model ever sees it — the only
   filter that prevents the hallucination rather than catching it.
2. **``no_speech_prob``**, the model's own estimate that a segment is not speech.
3. **Compression ratio**, which catches the repetition loops — the same phrase
   forty times — that a degenerate decode produces.
4. **Average log probability**, which catches a segment the model itself was
   unsure of.
5. **An exact match against known subtitle artefacts**, whole-segment only. Last
   because a phrase list is the crudest instrument here; whole-segment matching
   is what keeps it from ever touching a sentence someone actually said.

A dropped segment is **recorded, not erased**. It goes into the extraction event
under ``dropped`` with the reason, because "the model said this and it was
discarded" is provenance and a run of drops is how someone notices the
microphone was on the wrong input. It is not part of the transcript, so nothing
derived — no timeline row, no wiki sentence, no claim — can ever contain it.

**Nothing here computes anything about the content.** No summary, no keyword
extraction, no "this sounds like a medication". The transcript is the output.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, Sequence

from . import audio as audio_mod

log = logging.getLogger("agent.asr")

#: Bumped when anything below changes the words that come out. Part of the
#: settings digest, so a change re-transcribes rather than passing unnoticed.
ASR_VERSION = 1

DEFAULT_MODEL = "small"
DEFAULT_COMPUTE_TYPE = "int8"

#: Decoding. Near-zero temperature for reproducibility, and no conditioning on
#: previous text: conditioning is what turns one hallucinated segment into a
#: page of them, because the invention becomes the context for the next window.
BEAM_SIZE = 5
TEMPERATURE = 0.0
CONDITION_ON_PREVIOUS_TEXT = False

#: Above this, the model's own estimate is that the segment is not speech.
NO_SPEECH_THRESHOLD = 0.6
#: Above this, the text compresses too well to be language — a repetition loop.
COMPRESSION_RATIO_THRESHOLD = 2.4
#: Below this, the model was not confident of its own output.
LOG_PROB_THRESHOLD = -1.0

#: Whole-segment matches only, compared with punctuation and case removed. Every
#: one of these is a subtitle-corpus artefact Whisper emits over silence.
SUBTITLE_ARTEFACTS = frozenset(
    {
        "thank you",
        "thank you for watching",
        "thanks for watching",
        "thank you for watching this video",
        "please subscribe",
        "subscribe to my channel",
        "subtitles by the amaraorg community",
        "subtitles by the amara org community",
        "transcription by castingwordscom",
        "you",
        "bye",
        "bye bye",
    }
)

DROPPED_SILENCE = "not-speech"
DROPPED_RATIO = "repetition"
DROPPED_LOGPROB = "low-confidence"
DROPPED_ARTEFACT = "subtitle-artefact"

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


def _normalise(text: str) -> str:
    return " ".join(_PUNCTUATION.sub("", text).lower().split())


@dataclass(frozen=True)
class Word:
    """One word and the seconds it occupies. What a claim can cite."""

    start: float
    end: float
    word: str
    probability: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "word": self.word,
            "probability": round(self.probability, 4),
        }


@dataclass(frozen=True)
class Segment:
    """One segment that survived every filter."""

    start: float
    end: float
    text: str
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0
    compression_ratio: float = 0.0
    words: tuple[Word, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "no_speech_prob": round(self.no_speech_prob, 4),
            "avg_logprob": round(self.avg_logprob, 4),
            "compression_ratio": round(self.compression_ratio, 4),
            "words": [word.to_dict() for word in self.words],
        }


@dataclass(frozen=True)
class Dropped:
    """A segment the model produced and this module refused.

    The text is kept. It is provenance — the raw output, stored verbatim — and
    it is how someone notices a pattern of drops. It is never part of
    :attr:`Transcript.text`, so nothing derived from the transcript can contain
    it.
    """

    start: float
    end: float
    text: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Transcript:
    """What one recording said, and everything about how that was decided."""

    text: str = ""
    segments: tuple[Segment, ...] = ()
    dropped: tuple[Dropped, ...] = ()
    language: str = ""
    duration_s: float | None = None
    model: str = DEFAULT_MODEL
    compute_type: str = DEFAULT_COMPUTE_TYPE
    hotwords: tuple[str, ...] = ()
    settings: dict[str, Any] = field(default_factory=dict)
    #: Why there is no transcript, when there is none. A reported state: a
    #: missing library, a missing decoder, a file that would not decode. Never
    #: an exception reaching a capture path.
    unavailable: str | None = None

    @property
    def is_available(self) -> bool:
        return self.unavailable is None

    @property
    def has_speech(self) -> bool:
        return bool(self.text.strip())

    def to_payload(self) -> dict[str, Any]:
        """The verbatim record of this read, for ``extraction.completed``."""
        return {
            "transcript": self.text,
            "segments": [segment.to_dict() for segment in self.segments],
            "dropped": [item.to_dict() for item in self.dropped],
            "language": self.language,
            "duration_s": self.duration_s,
            "hotwords": list(self.hotwords),
            "settings": dict(self.settings),
            "unavailable": self.unavailable,
        }

    def describe(self) -> str:
        if not self.is_available:
            return f"not transcribed — {self.unavailable}"
        if not self.has_speech:
            dropped = len(self.dropped)
            if dropped:
                plural = "" if dropped == 1 else "s"
                return (
                    f"no speech — {dropped} segment{plural} the model produced were "
                    f"discarded as not speech"
                )
            return "no speech found in this recording"
        words = sum(len(segment.words) for segment in self.segments)
        return (
            f"{len(self.segments)} segments, {words} words"
            + (f", {len(self.dropped)} discarded" if self.dropped else "")
        )


def _settings_dict(model: str, compute_type: str, language: str) -> dict[str, Any]:
    """Everything that changes the words that come out. **Not the hotwords.**

    See the package docstring: putting the hotword list in here would make every
    new medication in the wiki re-transcribe the entire history of recordings.
    The list is recorded in the event beside this, so what was used is still
    answerable — it simply does not decide whether the work is done.
    """
    return {
        "version": ASR_VERSION,
        "model": model,
        "compute_type": compute_type,
        "language": language,
        "beam_size": BEAM_SIZE,
        "temperature": TEMPERATURE,
        "condition_on_previous_text": CONDITION_ON_PREVIOUS_TEXT,
        "vad_filter": True,
        "word_timestamps": True,
        "no_speech_threshold": NO_SPEECH_THRESHOLD,
        "compression_ratio_threshold": COMPRESSION_RATIO_THRESHOLD,
        "log_prob_threshold": LOG_PROB_THRESHOLD,
        "artefact_phrases": sorted(SUBTITLE_ARTEFACTS),
    }


def settings_digest(
    model: str = DEFAULT_MODEL,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    language: str = "en",
) -> str:
    """``sha256:…`` over the settings, standing where a prompt hash stands.

    The vision path keys idempotency on the prompt because that is what was
    asked of the model. Nothing is asked of Whisper in words, so the equivalent
    is the configuration that decides what it produces.
    """
    canonical = json.dumps(
        _settings_dict(model, compute_type, language),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Speech(Protocol):
    """What this module needs from a speech model.

    Narrow on purpose: it is the seam tests inject a stub through, and a wider
    one would make the stub a re-implementation of ``faster-whisper`` rather
    than a fixture.
    """

    def transcribe(self, path: Path, **options: Any) -> tuple[Sequence[Any], Any]:
        ...


#: Loaded models, by ``(name, compute_type)``. Loading ``small`` takes seconds
#: and allocates half a gigabyte; a queue of ten recordings must not do it ten
#: times. Held for the life of the process, which for the worker is the life of
#: the server, and never more than one entry in practice.
_LOADED: dict[tuple[str, str], Any] = {}
_LOAD_LOCK = threading.Lock()


def _load(model: str, compute_type: str) -> Any:
    """The real model, imported lazily and loaded once. Absence is a reported state."""
    with _LOAD_LOCK:
        cached = _LOADED.get((model, compute_type))
        if cached is not None:
            return cached
        loaded = _load_uncached(model, compute_type)
        _LOADED[(model, compute_type)] = loaded
        return loaded


def _load_uncached(model: str, compute_type: str) -> Any:
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415 - optional dependency
    except ImportError:
        raise audio_mod.DecodeUnavailable(
            "faster-whisper is not installed, so recordings are stored but not "
            "typed up. The audio is safe in raw/ and nothing is lost: install "
            "with `pip install 'health-agent[speech]'` and run "
            "`health-agent transcribe`."
        ) from None
    try:
        return WhisperModel(model, device="cpu", compute_type=compute_type)
    except Exception as exc:  # noqa: BLE001 - reported, never raised at a caller
        raise audio_mod.DecodeUnavailable(
            f"the speech model {model!r} could not be loaded: {exc}"
        ) from None


def _classify(segment: Any) -> str | None:
    """Which filter rejects this segment, if any. Order matters here."""
    if getattr(segment, "no_speech_prob", 0.0) > NO_SPEECH_THRESHOLD:
        return DROPPED_SILENCE
    if getattr(segment, "compression_ratio", 0.0) > COMPRESSION_RATIO_THRESHOLD:
        return DROPPED_RATIO
    if getattr(segment, "avg_logprob", 0.0) < LOG_PROB_THRESHOLD:
        return DROPPED_LOGPROB
    if _normalise(segment.text or "") in SUBTITLE_ARTEFACTS:
        return DROPPED_ARTEFACT
    return None


def _words(segment: Any) -> tuple[Word, ...]:
    return tuple(
        Word(
            start=float(word.start),
            end=float(word.end),
            word=str(word.word),
            probability=float(getattr(word, "probability", 0.0)),
        )
        for word in (getattr(segment, "words", None) or ())
    )


def transcribe(
    path: Path,
    language: str = "en",
    hotwords: Sequence[str] = (),
    model: str = DEFAULT_MODEL,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    speech: Speech | None = None,
) -> Transcript:
    """Read one recording. Returns a :class:`Transcript`, and never raises.

    *speech* is injected by tests. Left ``None``, the real model is loaded.

    ``language`` is **pinned**, never auto-detected: detection on ninety seconds
    of a noisy waiting room picks Welsh surprisingly often, and a transcript in
    the wrong language is not a rough transcript, it is a different document.
    """
    settings = _settings_dict(model, compute_type, language)
    empty = Transcript(
        language=language,
        model=model,
        compute_type=compute_type,
        hotwords=tuple(hotwords),
        settings=settings,
    )

    try:
        with audio_mod.working_copy(path) as copy:
            engine = speech if speech is not None else _load(model, compute_type)
            raw_segments, info = engine.transcribe(
                copy.path,
                language=language,
                beam_size=BEAM_SIZE,
                temperature=TEMPERATURE,
                condition_on_previous_text=CONDITION_ON_PREVIOUS_TEXT,
                vad_filter=True,
                word_timestamps=True,
                no_speech_threshold=NO_SPEECH_THRESHOLD,
                compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,
                log_prob_threshold=LOG_PROB_THRESHOLD,
                # An overlong bias list makes Whisper start inventing those
                # words in silence, which for a drug name is the worst failure
                # available. The cap is applied where the list is built.
                hotwords=" ".join(hotwords) if hotwords else None,
            )
            kept: list[Segment] = []
            dropped: list[Dropped] = []
            for segment in raw_segments:
                text = (segment.text or "").strip()
                reason = _classify(segment)
                if reason is not None or not text:
                    if text:
                        dropped.append(
                            Dropped(
                                start=float(segment.start),
                                end=float(segment.end),
                                text=text,
                                reason=reason or DROPPED_SILENCE,
                            )
                        )
                    continue
                kept.append(
                    Segment(
                        start=float(segment.start),
                        end=float(segment.end),
                        text=text,
                        no_speech_prob=float(getattr(segment, "no_speech_prob", 0.0)),
                        avg_logprob=float(getattr(segment, "avg_logprob", 0.0)),
                        compression_ratio=float(
                            getattr(segment, "compression_ratio", 0.0)
                        ),
                        words=_words(segment),
                    )
                )
            duration = getattr(info, "duration", None)
            return Transcript(
                text=" ".join(segment.text for segment in kept).strip(),
                segments=tuple(kept),
                dropped=tuple(dropped),
                language=str(getattr(info, "language", language) or language),
                duration_s=(
                    round(float(duration), 3) if duration is not None else copy.seconds
                ),
                model=model,
                compute_type=compute_type,
                hotwords=tuple(hotwords),
                settings=settings,
            )
    except audio_mod.DecodeUnavailable as exc:
        return replace(empty, unavailable=str(exc))
    except Exception as exc:  # noqa: BLE001 - a capture path must not raise
        # Reported, not raised. Capture already succeeded and the bytes are in
        # raw/; a transcription that fails leaves an artefact for a person to
        # look at, never a lost recording.
        log.exception("transcription of %s failed", path.name)
        return replace(
            empty, unavailable=f"this recording could not be transcribed: {exc}"
        )
