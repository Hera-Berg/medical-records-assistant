"""One recording in, one ``extraction.completed`` out.

The speech equivalent of :mod:`agent.extract.runner`, and deliberately the same
shape: read the artefact, produce events, let the caller decide when to append.
Two things about it are different, and both matter.

**It never touches the network.** No client, no endpoint, no credential. That is
the whole reason speech runs locally: a voice note recorded in a waiting room
must be typed up in that waiting room, with the box asleep and the laptop off
the tailnet. Everything that could fail here fails locally and says so.

**It emits no claims.** A transcript is evidence. Reading `40mg daily` out of it
is the vision-language model's job — it reads text as well as it reads a
photograph — and that needs the box, which this half of the pipeline exists to
do without. What it does instead, once a recording has words in it, is put the
recording back on the queue for :mod:`agent.extract.transcripts` to read when
the box is reachable.

Idempotency reuses :class:`agent.extract.propose.Key` unchanged, so
``completed_keys`` covers speech without knowing anything about it. The key is
``(artifact, settings digest, model)`` and the settings digest stands where a
prompt hash stands: nothing is asked of Whisper in words, so what was "asked" is
the configuration that decides what comes out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..events import envelope
from ..events.envelope import Event
from ..extract import jobs as jobs_mod
from ..extract import transcripts as transcripts_mod
from ..extract.propose import EXTRACTION_COMPLETED, Key, completed_keys
from ..ingest.mime import SPEECH_PREFIXES, is_speech
from ..projection import citations as citations_mod
from . import hotwords as hotwords_mod
from . import transcribe as transcribe_mod

log = logging.getLogger("agent.asr")

#: How a read turned out, mirroring the vision runner's vocabulary so the two
#: report the same way in the same queue.
READ_TRANSCRIBED = "transcribed"
READ_NO_SPEECH = "no-speech"
READ_ALREADY = "already-read"
READ_UNAVAILABLE = "no-reader"


@dataclass(frozen=True)
class Outcome:
    """What happened to one recording."""

    artifact: str
    state: str
    events: tuple[Event, ...] = ()
    reason: str | None = None
    reading: str | None = None
    transcript: transcribe_mod.Transcript | None = None

    def describe(self) -> str:
        detail = self.reason or ""
        if self.reading == READ_TRANSCRIBED and self.transcript is not None:
            detail = self.transcript.describe()
        elif self.reading == READ_NO_SPEECH:
            detail = "no speech found — nothing was recorded from it"
        label = {
            READ_TRANSCRIBED: "typed up",
            READ_NO_SPEECH: "silent",
            READ_ALREADY: "skipped",
            READ_UNAVAILABLE: "waiting",
        }.get(self.reading or "", self.state)
        return f"{label:<9} {self.artifact}  {detail}".rstrip()


def transcript_events(events: Iterable[Event]) -> dict[str, Event]:
    """The newest transcript for each artefact, from the log.

    One scan, shared with the reader that turns transcripts into claims: two
    copies would be two ideas of which transcript is current, and the two
    readers would disagree about what a re-transcription superseded.
    """
    return transcripts_mod.latest(events)


def build_event(
    device: str,
    key: Key,
    transcript: transcribe_mod.Transcript,
    artifact: citations_mod.Artifact,
    supersedes: str | None = None,
    ts: str | None = None,
) -> Event:
    """The transcript, stored verbatim, with everything needed to re-derive it."""
    payload: dict[str, Any] = {
        "key": key.as_dict(),
        "artifact": key.artifact,
        "reader": "speech",
        "mime": artifact.mime,
        **transcript.to_payload(),
    }
    if supersedes:
        # Why there are two reads of one recording. Without this the second
        # event looks like a duplicate rather than a deliberate re-read, and
        # nobody can tell which of them the record is showing.
        payload["supersedes"] = supersedes
        payload["forced"] = True
    return envelope.new(
        EXTRACTION_COMPLETED,
        device,
        ts=ts,
        provenance={
            "model": key.model,
            # A local model, so unlike the box's the identity really is pinnable
            # — the name and the quantisation together are what produced these
            # words.
            "model_rev": f"{transcript.model}/{transcript.compute_type}",
            "prompt_hash": key.prompt_hash,
            "artifact": key.artifact,
        },
        payload=payload,
    )


class Transcriber:
    """Reads recordings in one vault, with one set of settings."""

    def __init__(
        self,
        vault,
        speech: transcribe_mod.Speech | None = None,
        model: str = transcribe_mod.DEFAULT_MODEL,
        compute_type: str = transcribe_mod.DEFAULT_COMPUTE_TYPE,
        language: str | None = None,
    ):
        self.vault = vault
        self.speech = speech
        self.model = model
        self.compute_type = compute_type
        self.language = language or vault.config.locale

    @property
    def digest(self) -> str:
        return transcribe_mod.settings_digest(
            self.model, self.compute_type, self.language
        )

    def key_for(self, short: str) -> Key:
        return Key(artifact=short, prompt_hash=self.digest, model=self.model)

    def hotwords(self, events: Sequence[Event] | None = None) -> tuple[str, ...]:
        """The biasing list, from the record as it stands right now.

        Built per transcription rather than cached: the point of it is that it
        improves as the record grows, and a list captured at startup would be
        the empty one from a fresh vault for the whole of a session.
        """
        from ..projection import project  # noqa: PLC0415 - import cycle avoidance

        read = list(events) if events is not None else list(self.vault.read().events)
        # `as_of` is irrelevant to a list of names, but the projection is a pure
        # function of (events, as_of) and will not be called without one. The
        # artefact's own ingest time is used rather than a clock read, so this
        # stays free of the wall clock like everything else derived.
        moment = envelope.parse_ts_or_none(read[-1].ts) if read else None
        projection = project(read, moment)
        return hotwords_mod.collect(projection.entities)

    def run(
        self,
        short: str,
        events: Sequence[Event] | None = None,
        force: bool = False,
    ) -> Outcome:
        """Read one recording. Appends nothing; returns what should be appended."""
        log_events = list(events) if events is not None else list(self.vault.read().events)
        artifact = citations_mod.index_artifacts(log_events).get(short)
        if artifact is None:
            return Outcome(
                short,
                jobs_mod.NEEDS_ATTENTION,
                reason=(
                    f"no artifact.ingested event describes {short}, so there is "
                    f"nothing to transcribe and nothing a claim could cite"
                ),
            )
        if not is_speech(artifact.mime):
            return Outcome(
                short,
                jobs_mod.NEEDS_ATTENTION,
                reason=f"{short} is {artifact.mime}, which the speech model does not read",
            )

        path = self.vault.raw.resolve_recorded(artifact.rel)
        if path is None or not path.exists():
            return Outcome(
                short,
                jobs_mod.NEEDS_ATTENTION,
                reason=(
                    f"the bytes for {short} are not in raw/ at {artifact.rel}. The "
                    f"event is intact, so this is a missing file rather than a lost "
                    f"record — restore it from a backup or the sync client's trash, "
                    f"then choose \u201cTry typing it up again\u201d on its page"
                ),
            )

        key = self.key_for(short)
        previous = transcript_events(log_events).get(short)
        if not force and key.tuple in completed_keys(log_events):
            return Outcome(
                short,
                jobs_mod.DONE,
                reading=READ_ALREADY,
                reason=(
                    f"already typed up by {self.model} under settings "
                    f"{key.prompt_hash[7:19]}; the transcript is in the log, so "
                    f"nothing was read again. `transcribe --force` re-reads it with "
                    f"the current hotword list"
                ),
            )

        transcript = transcribe_mod.transcribe(
            Path(path),
            language=self.language,
            hotwords=self.hotwords(log_events),
            model=self.model,
            compute_type=self.compute_type,
            speech=self.speech,
        )

        if not transcript.is_available:
            # Not a failure of the recording and not a retry the queue can fix
            # by waiting: something is missing on this machine. The audio is
            # safe, and the job says what to install.
            return Outcome(
                short,
                jobs_mod.NEEDS_ATTENTION,
                reason=transcript.unavailable,
                reading=READ_UNAVAILABLE,
                transcript=transcript,
            )

        event = build_event(
            device=self.vault.identity.id,
            key=key,
            transcript=transcript,
            artifact=artifact,
            supersedes=previous.id if (force and previous is not None) else None,
        )
        return Outcome(
            short,
            jobs_mod.DONE,
            events=(event,),
            reading=READ_TRANSCRIBED if transcript.has_speech else READ_NO_SPEECH,
            transcript=transcript,
        )


@dataclass(frozen=True)
class DrainReport:
    """What one pass over the speech half of the queue did."""

    outcomes: tuple[Outcome, ...] = ()
    appended: int = 0
    idle_reason: str | None = None

    def describe(self) -> Sequence[str]:
        return [outcome.describe() for outcome in self.outcomes]


def speech_jobs(vault, queue: jobs_mod.Queue, moment=None) -> tuple[jobs_mod.Job, ...]:
    """The ready jobs whose artefacts are recordings.

    Partitioned by the artefact's mime rather than by a field on the job, so the
    job file's format is unchanged and a queue written by an older version is
    still drained correctly by this one.
    """
    artifacts = citations_mod.index_artifacts(list(vault.read().events))
    return tuple(
        job
        for job in queue.ready(moment)
        if (artifact := artifacts.get(job.artifact)) is not None
        and is_speech(artifact.mime)
    )


def drain(vault, transcriber: Transcriber, queue: jobs_mod.Queue, moment=None) -> DrainReport:
    """Transcribe every ready recording in the queue.

    Appends as it goes, one artefact at a time, so a crash halfway through a
    batch leaves the recordings it finished typed up rather than losing all of
    them. Nothing here can park the queue: there is no authentication to fail
    and no box to be asleep.
    """
    ready = speech_jobs(vault, queue, moment)
    if not ready:
        return DrainReport(idle_reason="no recordings are waiting to be typed up")

    outcomes: list[Outcome] = []
    appended = 0
    events = list(vault.read().events)
    for job in ready:
        queue.started(job)
        outcome = transcriber.run(job.artifact, events=events)
        outcomes.append(outcome)
        for event in outcome.events:
            vault.append(event)
            events.append(event)
            appended += 1
        if outcome.state == jobs_mod.DONE:
            queue.finished(job, key=transcriber.key_for(job.artifact).as_dict())
            if outcome.reading == READ_TRANSCRIBED:
                # Typed up, and now ordinary work for the reader that proposes
                # claims — which needs the box, so it goes back on the queue
                # rather than running here. A recording with no speech in it is
                # deliberately not re-queued: there is nothing to read, and a
                # job that can only ever come back "nothing found" is inbox debt
                # in the queue instead of the inbox.
                queue.add(job.artifact)
        else:
            # `unreadable` rather than a retry: a missing library or a file that
            # will not decode does not become decodable by being tried again in
            # thirty seconds, and a queue that retries it for ever hides it.
            queue.unreadable(job, outcome.reason or "this recording was not typed up")
    return DrainReport(outcomes=tuple(outcomes), appended=appended)
