"""One artefact in, events out. The whole extraction pipeline, in order.

Deterministic end to end, and that is a requirement rather than a style: a
nondeterministic ingest path breaks byte-identical rebuild, which every other
guarantee in the project rests on. There is no agent loop here, no planner and no
tool calling. Read the bytes, prepare the pages, ask once per page, validate,
cross-check, append.

The order is chosen so that failure never loses anything:

1. **The idempotency key is checked against the log first.** Work already done is
   not done again, however many times a job is retried or restarted.
2. **Preprocessing and the deterministic read happen before any network call**,
   so a sleeping box costs nothing that has to be redone.
3. **The model is asked once per page.**
4. **Events are appended last, together.** A crash before this point leaves the
   artefact queued and nothing recorded, which is recoverable. The reverse would
   leave claims whose extraction event never landed.

Capture never depends on any of this. Artefacts land in ``raw/`` and jobs persist
to ``.agent/jobs.jsonl`` whether or not the endpoint answers.
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ContextManager, Sequence

from ..errors import (
    AuthRejected,
    EndpointNotPrivate,
    EndpointUnreachable,
    ExtractionError,
    InferenceError,
    ModelIdentityMismatch,
    OutputTruncated,
    RateLimited,
)
from ..events.envelope import Event
from ..ingest.mime import is_speech
from ..llm import redaction
from ..llm.client import Client, Completion, parse_completion
from ..projection import citations as citations_mod
from . import (
    budget as budget_mod,
    crossverify,
    images,
    jobs as jobs_mod,
    prompts as prompts_mod,
    propose,
    text,
    transcripts as transcripts_mod,
)
from .schema import EXTRACTION_SCHEMA, SCHEMA_NAME
from .validate import Extraction, merge, read as read_answer

log = logging.getLogger("agent.extract")


#: How a read turned out, as distinct from what the queue did about it.
#:
#: The queue state answers "will this run again"; these answer "what happened
#: when the model was asked". They were the same field once, and the cost was
#: that ``0 claims proposed`` read identically whether the page stated nothing
#: the record tracks, the answer was refused by the validator, or the server
#: emitted thinking narration where JSON was expected. Each of those wants a
#: different next action — none, retake the photograph, fix the server — and
#: only some of them are a fault at all.
READ_CLAIMS = "claims"
READ_NOTHING = "nothing-clinical"
READ_REFUSED = "output-refused"
READ_UNREADABLE = "page-unreadable"
#: The answer was cut off at a token ceiling with nowhere left to raise it. Not
#: a page the model could not read and not an answer it got wrong: an answer it
#: never finished. Its own outcome because the next action is different again —
#: more room, or a person, rather than a retake or a server setting.
READ_TRUNCATED = "output-truncated"
READ_ALREADY = "already-read"
#: A recording that has not been typed up yet. Not a failure of anything: the
#: speech model runs locally and on its own schedule, and this reader has
#: nothing to read until it has.
READ_UNTRANSCRIBED = "not-typed-up"


@dataclass(frozen=True)
class Outcome:
    """What happened to one artefact."""

    artifact: str
    state: str
    events: tuple[Event, ...] = ()
    reason: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: One of the ``READ_*`` constants where the artefact actually reached the
    #: model, ``None`` where it did not get that far — an unreachable box, a
    #: missing file, an artefact this build has no reader for.
    reading: str | None = None

    @property
    def claims(self) -> int:
        return sum(1 for event in self.events if event.type == propose.CLAIM_PROPOSED)

    def describe(self) -> str:
        """One line, saying what happened rather than only what was appended.

        The label is padded but always followed by a space: ``needs-attention``
        and ``unreachable`` are longer than the column, and running the label
        into the hash produced ``unreadable3be48e``.
        """
        detail = self.reason or ""
        if self.reading == READ_CLAIMS:
            plural = "" if self.claims == 1 else "s"
            detail = f"{self.claims} claim{plural} proposed"
        elif self.reading == READ_NOTHING:
            detail = (
                "nothing clinical found — the page was read and states nothing "
                "this record tracks"
            )
        elif self.reading == READ_REFUSED:
            detail = f"output rejected: {detail}"
        elif self.reading == READ_UNREADABLE:
            detail = f"the model could not read it — review manually: {detail}"
        elif self.reading == READ_TRUNCATED:
            detail = f"ran out of room — review manually: {detail}"
        label = {
            READ_CLAIMS: "read",
            READ_NOTHING: "read",
            READ_REFUSED: "read",
            READ_UNREADABLE: "read",
            READ_TRUNCATED: "read",
            READ_ALREADY: "skipped",
        }.get(self.reading or "", self.state)
        return f"{label:<9} {self.artifact}  {detail}".rstrip()


def _short_hash(value: str) -> str:
    """``sha256:1f3c9a2b4d5e`` — enough of a digest to match one by eye.

    Slicing the whole string instead gives ``sha256:82714``, which is five hex
    characters wearing the prefix's clothes and matches nothing a person could
    grep for.
    """
    algorithm, _, digest = value.partition(":")
    return f"{algorithm}:{digest[:12]}" if digest else value[:12]


def _reading_of(extraction: Extraction) -> str:
    """Which ``READ_*`` outcome one merged extraction is.

    Five cases, and the reason they are not collapsed is that each sends the
    reader somewhere different: nothing to do, a server or prompt to fix, a
    photograph to retake, a page with more on it than one answer can hold, or
    claims to review.
    """
    if extraction.truncated:
        return READ_TRUNCATED
    if not extraction.readable:
        return READ_REFUSED if extraction.refused else READ_UNREADABLE
    if extraction.claims:
        return READ_CLAIMS
    # Readable, no claims. Either every claim on the page was individually
    # rejected — a subject that resolves to nothing, a value that failed the
    # schema — or the page genuinely says nothing the record tracks.
    return READ_REFUSED if extraction.rejections else READ_NOTHING


def _state_of(extraction: Extraction) -> str:
    """What the queue should do about one merged extraction.

    ``needs-attention`` for a cut-off answer rather than ``unreadable``: nothing
    was wrong with the artefact, and calling it unreadable would tell the owner
    to retake a photograph that is perfectly legible. It is terminal all the
    same — retrying it unchanged asks the same question and gets the same
    truncated answer.
    """
    if extraction.truncated:
        return jobs_mod.NEEDS_ATTENTION
    return jobs_mod.DONE if extraction.readable else jobs_mod.UNREADABLE


class Extractor:
    """Reads artefacts with one configured client, into one vault."""

    def __init__(self, vault, client: Client, locale: str = "en"):
        self.vault = vault
        self.client = client
        self.locale = locale
        redaction.install()

    def _runtime(self, started: float) -> dict[str, Any]:
        """What read this artefact, and how long it took, as observations.

        The client says what it talks to; the wall time is measured here, over
        the whole artefact rather than one page's request, because "about 70
        seconds a document on this machine" is the number a person waiting for
        a queue needs.
        """
        facts = dict(getattr(self.client, "runtime", None) or {"kind": "endpoint"})
        facts["elapsed_s"] = round(time.monotonic() - started, 3)
        return facts

    # -- reading one artefact ---------------------------------------------

    def _artifact(self, short: str) -> citations_mod.Artifact | None:
        read = self.vault.read()
        return citations_mod.index_artifacts(read.events).get(short)

    def _path(self, artifact: citations_mod.Artifact) -> Path | None:
        return self.vault.raw.resolve_recorded(artifact.rel)

    def run(self, short: str, events: Sequence[Event] | None = None) -> Outcome:
        """Read one artefact and return the events it wants appended.

        Appends nothing itself. The caller decides when to write, which is what
        lets a dry run produce exactly what a real run would.
        """
        started = time.monotonic()
        log_events = list(events) if events is not None else list(self.vault.read().events)
        artifact = citations_mod.index_artifacts(log_events).get(short)
        if artifact is None:
            return Outcome(
                short,
                jobs_mod.NEEDS_ATTENTION,
                reason=(
                    f"no artifact.ingested event describes {short}, so there is nothing "
                    f"to read and nothing a claim could cite"
                ),
            )

        if is_speech(artifact.mime):
            # A recording is read from its transcript, not from its bytes. The
            # audio never goes over the wire: the local speech model already
            # turned it into words, and words are what this model reads.
            return self._run_transcript(short, artifact, log_events, started)

        path = self._path(artifact)
        if path is None or not path.exists():
            return Outcome(
                short,
                jobs_mod.NEEDS_ATTENTION,
                reason=(
                    f"the bytes for {short} are not in raw/ at {artifact.rel}. The "
                    f"event is intact, so this is a missing file rather than a lost "
                    f"record — restore it and re-run, or run `health-agent check`"
                ),
            )

        settings = self.client.settings
        document = images.prepare_artifact(path, artifact.mime, settings.long_edge)
        if not document.is_readable:
            # Never reached the model: there was nothing to send it. Deliberately
            # not READ_UNREADABLE, which means the model looked and could not
            # make it out.
            return Outcome(
                short, jobs_mod.UNREADABLE, reason=document.unreadable
            )

        page_prompts = [
            prompts_mod.build(page, total_pages=len(document.pages))
            for page in document.pages
        ]
        key = propose.Key(
            artifact=short,
            prompt_hash=page_prompts[0].prompt_hash,
            model=settings.model,
        )
        already = propose.completed_keys(log_events)
        if key.tuple in already:
            return Outcome(
                short,
                jobs_mod.DONE,
                reading=READ_ALREADY,
                reason=(
                    f"already read by {settings.model} under prompt "
                    f"{_short_hash(key.prompt_hash)}; its claims are already in the "
                    f"log, so nothing was sent and nothing appended. Change the model "
                    f"or the prompt to re-derive it"
                ),
            )

        # Before the network: a sleeping box must not cost this work twice.
        deterministic = text.read(path, artifact.mime)

        answers: list[Extraction] = []
        budget_notes: list[str] = []
        completion = None
        for page, prompt in zip(document.pages, page_prompts):
            where = f"page {page.page}" if len(document.pages) > 1 else None
            completion, raised = self._ask(
                prompt.to_list(), EXTRACTION_SCHEMA, SCHEMA_NAME, where=where
            )
            budget_notes.extend(raised)
            answers.append(self._read_one(completion, artifact.mime, where=where))
            log.info(
                "read %s page %s: %d claims, ~%d vision tokens",
                short,
                page.page,
                len(answers[-1].claims),
                page.vision_tokens,
            )

        extraction = merge(answers)
        if budget_notes:
            extraction = replace(
                extraction, notes=extraction.notes + tuple(dict.fromkeys(budget_notes))
            )
        verifications = crossverify.verify_all(extraction.claims, deterministic)
        proposal = propose.build(
            device=self.vault.identity.id,
            key=key,
            completion=completion,
            extraction=extraction,
            prompts_used=page_prompts,
            artifact=artifact,
            already=already,
            verifications=verifications,
            image_notes=[page.describe() for page in document.pages],
            deterministic=deterministic.describe(),
            seen_models=propose.observed_models(log_events),
            runtime=self._runtime(started),
        )
        state = _state_of(extraction)
        reading = _reading_of(extraction)
        reason = extraction.unreadable_reason
        if reading == READ_REFUSED and reason is None:
            # Readable page, and every claim on it individually refused. The
            # reasons are the whole content of that outcome, so they are the
            # line rather than a count of them.
            reason = "; ".join(extraction.describe_rejections())
        return Outcome(
            short,
            state,
            events=proposal.events,
            reason=reason,
            # A note that restates the reason is printed directly under the line
            # that already said it, which teaches a reader to skip the notes —
            # and the notes are where cross-check disagreements are reported.
            notes=tuple(
                note for note in proposal.notes if reason is None or reason not in note
            ),
            reading=reading,
        )

    def _run_transcript(
        self,
        short: str,
        artifact: citations_mod.Artifact,
        log_events: Sequence[Event],
        started: float,
    ) -> Outcome:
        """Read one recording's transcript as text.

        Deferred out of phase 6 on purpose: capturing and typing up a voice note
        must work with the box asleep, so the local half stops at the words.
        Proposing a claim from them is this model reading text and needs the
        tailnet, which is why it is here and not there.

        Everything a recording says is ``patient-reported``. That is settled
        three times over — the schema permits no other value, the validator caps
        by mime, and :func:`agent.extract.transcripts.force_tier` holds anything
        that still got through. Tier decides ranking, and a voice note that
        outranked a prescription would quietly beat the script in the wiki.
        """
        transcript_event = transcripts_mod.latest(log_events).get(short)
        if transcript_event is None:
            return Outcome(
                short,
                jobs_mod.NEEDS_ATTENTION,
                reading=READ_UNTRANSCRIBED,
                reason=(
                    f"{short} is a recording that has not been typed up yet. The "
                    f"speech model runs on this machine and needs no box — run "
                    f"`health-agent transcribe`, and this reads the words it produces"
                ),
            )

        payload = transcripts_mod.transcript_payload(transcript_event) or {}
        spoken = transcripts_mod.spoken_text(payload)
        if not spoken:
            # Silence, or forty seconds of a cough. Nothing was asked of the
            # model, because there is nothing to ask about — and a hallucinated
            # segment the speech runner already discarded must not be read back
            # in here through some other field.
            return Outcome(
                short,
                jobs_mod.DONE,
                reading=READ_NOTHING,
                reason=(
                    "this recording holds no speech, so there was nothing to read "
                    "and nothing was sent"
                ),
            )

        settings = self.client.settings
        prompt = transcripts_mod.build(spoken)
        key = propose.Key(
            artifact=short, prompt_hash=prompt.prompt_hash, model=settings.model
        )
        already = propose.completed_keys(log_events)
        if key.tuple in already:
            return Outcome(
                short,
                jobs_mod.DONE,
                reading=READ_ALREADY,
                reason=(
                    f"these words have already been read by {settings.model} under "
                    f"prompt {_short_hash(key.prompt_hash)}. Re-transcribing the "
                    f"recording to different words would read them again; the same "
                    f"words are the same work"
                ),
            )

        completion, raised = self._ask(
            prompt.to_list(),
            transcripts_mod.TRANSCRIPT_SCHEMA,
            transcripts_mod.SCHEMA_NAME,
        )
        extraction = self._read_one(completion, artifact.mime)
        if raised:
            extraction = replace(extraction, notes=extraction.notes + tuple(raised))
        held, tier_notes = transcripts_mod.force_tier(extraction.claims)
        if tier_notes:
            extraction = replace(
                extraction,
                claims=tuple(held),
                notes=extraction.notes + tuple(dict.fromkeys(tier_notes)),
            )

        words = transcripts_mod.words_of(payload)
        located = [transcripts_mod.locate(c.source_span, words) for c in extraction.claims]
        spans = {
            index: span.to_payload()
            for index, span in enumerate(located)
            if span is not None
        }

        log.info(
            "read transcript %s: %d claims, %d located in the audio",
            short,
            len(extraction.claims),
            len(spans),
        )

        proposal = propose.build(
            device=self.vault.identity.id,
            key=key,
            completion=completion,
            extraction=extraction,
            prompts_used=[prompt],
            artifact=artifact,
            already=already,
            seen_models=propose.observed_models(log_events),
            audio_spans=spans,
            runtime=self._runtime(started),
            extra={
                "reader": "transcript",
                # Which transcript these claims were read from. A better speech
                # model will produce different words for the same recording, and
                # "which reading of the audio did this come from" has to stay
                # answerable across that.
                "transcript_event": transcript_event.id,
                "transcript": spoken,
                "audio": transcripts_mod.describe(words, located),
            },
        )
        return Outcome(
            short,
            _state_of(extraction),
            events=proposal.events,
            reason=extraction.unreadable_reason,
            notes=proposal.notes,
            reading=_reading_of(extraction),
        )

    def _ask(
        self,
        messages: list[dict[str, Any]],
        schema: Any,
        schema_name: str,
        where: str | None = None,
    ) -> tuple[Completion, tuple[str, ...]]:
        """Ask once, and again with more room if the box says it ran out.

        The ladder is in :mod:`agent.extract.budget`; what belongs here is why
        the loop is shaped this way. It advances only on the server's own
        ``finish_reason``, never on a guess about how long an answer should be,
        so an ordinary page costs exactly one call and a four-table pathology
        report costs one more. It stops at the top of the ladder or at the edge
        of ``ctx``, whichever comes first, because the same prompt at temperature
        zero produces the same cut-off answer and looping would only spend the
        box's time.

        The raise is returned as a note rather than left in the log alone: "this
        page needed two calls" is provenance, and the extraction event records
        the ceiling it finally answered under.
        """
        ceiling = budget_mod.START
        notes: list[str] = []
        prefix = f"{where}: " if where else ""
        while True:
            completion = self.client.complete(
                messages, schema=schema, schema_name=schema_name, max_tokens=ceiling
            )
            if not completion.truncated:
                return completion, tuple(notes)
            ctx = self.client.settings.ctx
            nxt = budget_mod.next_budget(ceiling, ctx, completion.prompt_tokens)
            if nxt is None:
                return completion, tuple(notes)
            log.info(
                "answer cut off at %d tokens%s; asking again with %d",
                ceiling,
                f" ({where})" if where else "",
                nxt,
            )
            notes.append(prefix + budget_mod.describe_raise(ceiling, nxt))
            ceiling = nxt

    def _read_one(self, completion, mime: str, where: str | None = None) -> Extraction:
        """Parse and validate one page's answer. Never repairs, never raises."""
        try:
            payload = parse_completion(completion)
        except OutputTruncated as exc:
            # Separated from every other refusal on purpose. The answer is not
            # wrong, it is unfinished, and the fix is room rather than a server
            # setting — the generic message would send someone to read about
            # guided decoding while the token cap sat there unexamined.
            prefix = f"{where}: " if where else ""
            return Extraction(
                readable=False,
                refused=True,
                truncated=True,
                unreadable_reason=(
                    prefix
                    + str(exc)
                    + " "
                    + budget_mod.describe_exhausted(
                        completion.max_tokens or budget_mod.START,
                        self.client.settings.ctx,
                        completion.prompt_tokens,
                    )
                ),
            )
        except InferenceError as exc:
            # The raw output is still recorded by the caller. An answer that is
            # not JSON is a refusal, not a crash — and not a page the model could
            # not read, which is why `refused` is set: thinking narration landing
            # in `content` is a server setting to change, and reporting it as an
            # unreadable photograph would send the user to the wrong place.
            return Extraction(
                readable=False,
                refused=True,
                unreadable_reason=str(exc),
            )
        return read_answer(payload, mime=mime, locale=self.locale)


# --- draining the queue ----------------------------------------------------


@dataclass(frozen=True)
class DrainReport:
    """What one pass over the queue did."""

    outcomes: tuple[Outcome, ...] = ()
    appended: int = 0
    parked: tuple[jobs_mod.Job, ...] = ()
    parked_reason: str | None = None
    #: Why nothing ran, when nothing ran. **Never ``None`` on an empty pass.**
    #: "appended 0 events, 0 claims proposed" with no lines under it is
    #: indistinguishable from a broken command, and a run that does nothing has
    #: a reason for doing nothing every single time.
    idle_reason: str | None = None

    @property
    def is_parked(self) -> bool:
        return bool(self.parked)

    def stats(self) -> dict[str, Any]:
        counted: dict[str, int] = {}
        readings: dict[str, int] = {}
        for outcome in self.outcomes:
            counted[outcome.state] = counted.get(outcome.state, 0) + 1
            if outcome.reading is not None:
                readings[outcome.reading] = readings.get(outcome.reading, 0) + 1
        return {
            "artifacts": len(self.outcomes),
            "events_appended": self.appended,
            "claims": sum(outcome.claims for outcome in self.outcomes),
            "by_state": counted,
            "by_reading": readings,
            "parked": len(self.parked),
        }


def drain(
    vault,
    extractor: Extractor,
    queue: jobs_mod.Queue,
    limit: int | None = None,
    moment: datetime | None = None,
    only: Sequence[str] | None = None,
    lock: Callable[[], ContextManager[Any]] = contextlib.nullcontext,
    should_yield: Callable[[], bool] = lambda: False,
) -> DrainReport:
    """Work the queue until it is empty, parked, or *limit* jobs have run.

    *lock* is held around every write — the queue's lines and the log's events —
    and **never around the model call**. A capture appends under the same lock,
    and ``CLAUDE.md`` is explicit that a capture returns before any inference
    runs: holding the lock across a read would make a photograph dropped into
    the window wait out a minute of someone else's document on a laptop CPU.

    *should_yield* is asked before each job. The reader on this computer has one
    slot, and a person waiting for an answer to a question goes before the next
    queued document rather than behind the whole queue.

    The three failure kinds do three different things, which is the point of
    keeping them apart all the way from the client: an auth rejection parks
    everything, an unreachable box backs the job off and leaves it queued, and a
    model identity mismatch stops that job for a person to look at.

    *only* restricts the pass to named artefacts. ``--limit`` cannot choose
    *which* artefact runs, and re-running one specific image is what checking a
    bad read actually consists of.
    """
    now = moment or datetime.now(timezone.utc)
    wanted = set(only) if only is not None else None
    outcomes: list[Outcome] = []
    appended = 0
    # One queue, two readers, and a recording passes through both. Until the
    # speech model has typed it up there is nothing here to read, so it is
    # passed over silently and *not* marked on the job — the other drain still
    # has to pick it up. Once a transcript exists the recording is ordinary work
    # for this reader, which reads the words rather than the audio.
    log_events = list(vault.read().events)
    artifacts = citations_mod.index_artifacts(log_events)
    transcribed = set(transcripts_mod.latest(log_events))

    if queue.is_parked:
        return DrainReport(
            parked=queue.blocked(),
            parked_reason=(
                "the queue is parked: authentication was rejected by the inference "
                "box. Set a new key, then run `health-agent extract --resume`."
            ),
        )

    for job in queue.ready(now):
        if wanted is not None and job.artifact not in wanted:
            continue
        found = artifacts.get(job.artifact)
        if found is not None and is_speech(found.mime) and job.artifact not in transcribed:
            continue
        if limit is not None and len(outcomes) >= limit:
            break
        if should_yield():
            break
        with lock():
            running = queue.started(job)
        try:
            outcome = extractor.run(job.artifact)
        except AuthRejected as exc:
            # Terminal, and it stops every job that needs the box rather than
            # this one job. Recordings are excluded: they are read locally, and
            # a rejected key has nothing to do with them.
            reason = redaction.scrub(str(exc))
            with lock():
                parked = queue.park_for_auth(
                    reason,
                    artifacts=[
                        short
                        for short in artifacts
                        if not is_speech(artifacts[short].mime) or short in transcribed
                    ],
                )
            return DrainReport(
                outcomes=tuple(outcomes),
                appended=appended,
                parked=parked,
                parked_reason=reason,
            )
        except (EndpointUnreachable, RateLimited) as exc:
            with lock():
                queue.failed(running, redaction.scrub(str(exc)), moment=now)
            outcomes.append(
                Outcome(job.artifact, jobs_mod.UNREACHABLE, reason=redaction.scrub(str(exc)))
            )
            continue
        except (ModelIdentityMismatch, EndpointNotPrivate) as exc:
            # Neither is retryable and neither is the queue's fault. A person
            # has to change a config file or a server.
            with lock():
                queue.update(running, jobs_mod.NEEDS_ATTENTION, redaction.scrub(str(exc)))
            outcomes.append(
                Outcome(
                    job.artifact,
                    jobs_mod.NEEDS_ATTENTION,
                    reason=redaction.scrub(str(exc)),
                )
            )
            continue
        except InferenceError as exc:
            with lock():
                queue.failed(running, redaction.scrub(str(exc)), moment=now)
            outcomes.append(
                Outcome(job.artifact, jobs_mod.UNREACHABLE, reason=redaction.scrub(str(exc)))
            )
            continue

        with lock():
            for event in outcome.events:
                vault.append(event)
                appended += 1
            if outcome.state == jobs_mod.UNREADABLE:
                queue.unreadable(running, outcome.reason or "the model could not read it")
            elif outcome.state == jobs_mod.NEEDS_ATTENTION:
                queue.update(running, jobs_mod.NEEDS_ATTENTION, outcome.reason)
            else:
                queue.finished(running, key=None)
        outcomes.append(outcome)

    return DrainReport(
        outcomes=tuple(outcomes),
        appended=appended,
        idle_reason=(
            None if outcomes else explain_idle(vault, queue, now, only=only, limit=limit)
        ),
    )


def explain_idle(
    vault,
    queue: jobs_mod.Queue,
    moment: datetime,
    only: Sequence[str] | None = None,
    limit: int | None = None,
) -> str:
    """Why a pass over the queue ran nothing. Always a sentence, never silence.

    The states are checked from the outside in — the vault, then the log, then
    the queue — because that is the order in which they stop being the user's
    fault. "You have not ingested anything" and "everything has already been
    read" and "three jobs are waiting out a backoff" are three different next
    actions, and the run that produced all three used to print the same nothing.
    """
    events = list(vault.read().events)
    artifacts = citations_mod.index_artifacts(events)
    read_already = {key[0] for key in propose.completed_keys(events)}

    if only is not None:
        named = ", ".join(sorted(only))
        jobs = [queue.for_artifact(short) for short in sorted(only)]
        waiting = [job for job in jobs if job is not None and job.is_runnable]
        if waiting:
            soonest = min(job.not_before or "" for job in waiting)
            return (
                f"{named} is queued but not ready yet: a backoff from an earlier "
                f"failure has not elapsed"
                + (f", and lifts at {soonest}" if soonest else "")
            )
        return (
            f"nothing ran for {named}: no job for it is in a runnable state — see "
            f"`.agent/jobs.jsonl`"
        )

    if limit is not None and limit <= 0:
        return f"--limit {limit} means no artefact was read"

    if not artifacts:
        return (
            "there is nothing to read: no artefact.ingested event exists in this "
            "vault. Add a document with `health-agent ingest <file>` first"
        )

    unread = sorted(set(artifacts) - read_already)
    if not unread:
        return (
            f"nothing to read: all {len(artifacts)} artefact(s) in this vault have "
            f"already been extracted, and re-reading one under the same model and "
            f"prompt would propose the same claims a second time"
        )
    # Everything below is "some are unread but none of them ran". Leading with
    # how many *did* get read answers the question underneath the question — a
    # user looking at an empty pass wants to know whether their vault was read,
    # not only why this invocation was quiet. Omitted where none were: "0 of 3
    # have already been extracted" is a fact stated backwards.
    extracted = len(artifacts) - len(unread)
    done_note = (
        f"{extracted} of {len(artifacts)} artefact(s) have already been extracted; "
        if extracted
        else ""
    )

    # Recordings are not this reader's work. They are unread here and will stay
    # unread here however many times this runs, so saying "1 artefact has no
    # extraction" about one would send someone to debug the endpoint over a
    # voice note that is waiting for a local model.
    typed_up = set(transcripts_mod.latest(list(vault.read().events)))
    recordings = [
        short
        for short in unread
        if is_speech(artifacts[short].mime) and short not in typed_up
    ]
    if recordings:
        verb = "is a recording" if len(recordings) == 1 else "are recordings"
        note = (
            f"{len(recordings)} of them {verb}, which the speech model types up on "
            f"this machine — run `health-agent transcribe`"
        )
        unread = [short for short in unread if short not in set(recordings)]
        if not unread:
            return f"{done_note}{note}"
        done_note = f"{done_note}{note}; "

    # Unread, but the queue is not offering them. Either they are terminal —
    # already given up on, or found unreadable — or they are backing off.
    backing_off = [
        job for job in queue.runnable() if not job.ready_at(moment) and job.artifact in unread
    ]
    if backing_off:
        soonest = min(job.not_before or "" for job in backing_off)
        return (
            f"{done_note}{len(backing_off)} job(s) are waiting out a backoff after "
            f"an earlier failure; the first is ready at {soonest}. Nothing was sent"
        )

    terminal = [
        job
        for job in queue
        if job.artifact in unread and job.is_terminal and job.state != jobs_mod.DONE
    ]
    if terminal:
        counted: dict[str, int] = {}
        for job in terminal:
            counted[job.state] = counted.get(job.state, 0) + 1
        summary = ", ".join(f"{count} {state}" for state, count in sorted(counted.items()))
        verb = "is" if len(terminal) == 1 else "are"
        return (
            f"{done_note}the remaining {len(terminal)} {verb} not being retried "
            f"({summary}). `health-agent extract --artifact <hash>` re-runs one"
        )

    return (
        f"{done_note}{len(unread)} artefact(s) have no extraction and no job was "
        f"ready to run — see `.agent/jobs.jsonl`"
    )


def resolve_artifact(vault, token: str) -> str:
    """The short hash *token* names, or a refusal that says what to type.

    Accepts the short hash as written in a filename and in a citation, or any
    unambiguous prefix of one. A prefix matching two artefacts is refused with
    both named rather than resolved to whichever sorts first: re-running the
    wrong photograph and being told it read fine is worse than being asked to
    type two more characters.
    """
    artifacts = citations_mod.index_artifacts(list(vault.read().events))
    cleaned = token.strip().lower()
    if cleaned in artifacts:
        return cleaned
    matches = sorted(short for short in artifacts if short.startswith(cleaned))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ExtractionError(
            f"{token!r} matches {len(matches)} artefacts ({', '.join(matches)}); "
            f"type enough of the hash to name one"
        )
    raise ExtractionError(
        f"no artefact in this vault has the hash {token!r}. The hash is the six "
        f"characters before the extension in a raw/ filename, and the footnote key "
        f"in a wiki citation. `health-agent check` lists what is stored"
    )


def enqueue_artifacts(vault, queue: jobs_mod.Queue, shorts: Sequence[str]):
    """Queue named artefacts, re-opening a job that had stopped being retried.

    Deliberately more forceful than :func:`enqueue_unread`, which skips a
    terminal job. Naming an artefact is an explicit instruction to try it again,
    and the commonest reason to type one is that its job gave up. What this does
    *not* override is the log: an artefact already extracted under this model and
    prompt still reports as already read rather than proposing its claims twice.
    """
    queued = []
    for short in shorts:
        job = queue.for_artifact(short)
        if job is not None and job.is_terminal:
            queued.append(queue.update(job, jobs_mod.QUEUED, "re-run by name", attempts=0))
        else:
            queued.append(queue.add(short))
    return tuple(queued)


def enqueue_unread(vault, queue: jobs_mod.Queue) -> tuple[jobs_mod.Job, ...]:
    """Queue every ingested artefact the log has no extraction for.

    Reads the log rather than the queue, so an artefact ingested on another
    device — or on this one before the queue file existed — is picked up.
    """
    events = list(vault.read().events)
    artifacts = citations_mod.index_artifacts(events)
    done = {key[0] for key in propose.completed_keys(events)}
    added = []
    for short in sorted(artifacts):
        if short in done:
            continue
        job = queue.for_artifact(short)
        if job is not None:
            # Terminal: given up on or found unreadable, and only `--artifact`
            # re-opens it. Live: already waiting, and `queue.add` would hand
            # back the same job — counting it as newly queued makes "queued 2
            # artefact(s) not yet read" print on a run that queued nothing.
            continue
        added.append(queue.add(short))
    return tuple(added)
