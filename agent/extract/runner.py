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


@dataclass(frozen=True)
class Outcome:
    """What happened to one artefact."""

    artifact: str
    state: str
    events: tuple[Event, ...] = ()
    reason: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def claims(self) -> int:
        return sum(1 for event in self.events if event.type == propose.CLAIM_PROPOSED)

    def describe(self) -> str:
        if self.state == jobs_mod.DONE:
            return f"read     {self.artifact}  {self.claims} claims proposed"
        return f"{self.state:<9}{self.artifact}  {self.reason or ''}".rstrip()


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
                reason="already read by this model under this prompt",
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
        return Outcome(
            short,
            state,
            events=proposal.events,
            reason=None if extraction.readable else extraction.unreadable_reason,
            notes=proposal.notes,
        )

    def _read_one(self, completion, mime: str) -> Extraction:
        """Parse and validate one page's answer. Never repairs, never raises."""
        try:
            payload = parse_json_content(completion.content)
        except InferenceError as exc:
            # The raw output is still recorded by the caller. An answer that is
            # not JSON is an unreadable page, not a crash.
            return Extraction(
                readable=False,
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

    @property
    def is_parked(self) -> bool:
        return bool(self.parked)

    def stats(self) -> dict[str, Any]:
        counted: dict[str, int] = {}
        for outcome in self.outcomes:
            counted[outcome.state] = counted.get(outcome.state, 0) + 1
        return {
            "artifacts": len(self.outcomes),
            "events_appended": self.appended,
            "claims": sum(outcome.claims for outcome in self.outcomes),
            "by_state": counted,
            "parked": len(self.parked),
        }


def drain(
    vault,
    extractor: Extractor,
    queue: jobs_mod.Queue,
    limit: int | None = None,
    moment: datetime | None = None,
) -> DrainReport:
    """Work the queue until it is empty, parked, or *limit* jobs have run.

    The three failure kinds do three different things, which is the point of
    keeping them apart all the way from the client: an auth rejection parks
    everything, an unreachable box backs the job off and leaves it queued, and a
    model identity mismatch stops that job for a person to look at.
    """
    now = moment or datetime.now(timezone.utc)
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

    return DrainReport(outcomes=tuple(outcomes), appended=appended)


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
        if job is not None and job.is_terminal:
            continue
        added.append(queue.add(short))
    return tuple(added)
