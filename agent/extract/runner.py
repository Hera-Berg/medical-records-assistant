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

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..errors import (
    AuthRejected,
    EndpointNotPrivate,
    EndpointUnreachable,
    ExtractionError,
    InferenceError,
    ModelIdentityMismatch,
    RateLimited,
)
from ..events.envelope import Event
from ..llm import redaction
from ..llm.client import Client, parse_json_content
from ..projection import citations as citations_mod
from . import crossverify, images, jobs as jobs_mod, prompts as prompts_mod, propose, text
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
READ_ALREADY = "already-read"


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
        label = {
            READ_CLAIMS: "read",
            READ_NOTHING: "read",
            READ_REFUSED: "read",
            READ_UNREADABLE: "read",
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

    Four cases, and the reason they are not collapsed is that each sends the
    reader somewhere different: nothing to do, a server or prompt to fix, a
    photograph to retake, or claims to review.
    """
    if not extraction.readable:
        return READ_REFUSED if extraction.refused else READ_UNREADABLE
    if extraction.claims:
        return READ_CLAIMS
    # Readable, no claims. Either every claim on the page was individually
    # rejected — a subject that resolves to nothing, a value that failed the
    # schema — or the page genuinely says nothing the record tracks.
    return READ_REFUSED if extraction.rejections else READ_NOTHING


class Extractor:
    """Reads artefacts with one configured client, into one vault."""

    def __init__(self, vault, client: Client, locale: str = "en"):
        self.vault = vault
        self.client = client
        self.locale = locale
        redaction.install()

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
        completion = None
        for page, prompt in zip(document.pages, page_prompts):
            completion = self.client.complete(
                prompt.to_list(),
                schema=EXTRACTION_SCHEMA,
                schema_name=SCHEMA_NAME,
            )
            answers.append(self._read_one(completion, artifact.mime))
            log.info(
                "read %s page %s: %d claims, ~%d vision tokens",
                short,
                page.page,
                len(answers[-1].claims),
                page.vision_tokens,
            )

        extraction = merge(answers)
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
        )
        state = jobs_mod.DONE if extraction.readable else jobs_mod.UNREADABLE
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

    def _read_one(self, completion, mime: str) -> Extraction:
        """Parse and validate one page's answer. Never repairs, never raises."""
        try:
            payload = parse_json_content(completion.content)
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
) -> DrainReport:
    """Work the queue until it is empty, parked, or *limit* jobs have run.

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
        if limit is not None and len(outcomes) >= limit:
            break
        running = queue.started(job)
        try:
            outcome = extractor.run(job.artifact)
        except AuthRejected as exc:
            # Terminal, and it stops the whole queue rather than this one job.
            reason = redaction.scrub(str(exc))
            parked = queue.park_for_auth(reason)
            return DrainReport(
                outcomes=tuple(outcomes),
                appended=appended,
                parked=parked,
                parked_reason=reason,
            )
        except (EndpointUnreachable, RateLimited) as exc:
            queue.failed(running, redaction.scrub(str(exc)), moment=now)
            outcomes.append(
                Outcome(job.artifact, jobs_mod.UNREACHABLE, reason=redaction.scrub(str(exc)))
            )
            continue
        except (ModelIdentityMismatch, EndpointNotPrivate) as exc:
            # Neither is retryable and neither is the queue's fault. A person
            # has to change a config file or a server.
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
            queue.failed(running, redaction.scrub(str(exc)), moment=now)
            outcomes.append(
                Outcome(job.artifact, jobs_mod.UNREACHABLE, reason=redaction.scrub(str(exc)))
            )
            continue

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
