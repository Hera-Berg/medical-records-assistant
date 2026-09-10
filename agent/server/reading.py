"""Why something that has not been read has not been read.

:mod:`agent.projection.reading` answers "has this been read, and what came of
it" from the event log, which is what gets written into ``wiki/``. This module
answers the other half — *why not yet* — from the extraction queue and the last
known state of the inference box, neither of which belongs in a generated file:
the queue is a disposable cache of work, and a rebuild after deleting it has to
produce identical bytes.

The distinction is worth keeping sharp, because the two answers age differently.
"Not read yet" is true for as long as it is true. "The box is asleep" is true
for about as long as it takes to read it, and writing it into a file would leave
a record asserting, permanently, that a Mac was asleep one afternoon in
September.

What this adds, in the order it matters to someone waiting:

* **A rejected key.** Needs a person; nothing drains until one acts.
* **Nothing configured to read it.** A vault with no endpoint is a legitimate
  vault — a demo one is deliberately one — and it is not the same as a box that
  is asleep.
* **The box is asleep.** The expected condition, and the one that resolves
  itself. Said quietly.
* **Behind other files.** The ordinary case: it is queued and its turn is
  coming.

Recordings are excluded from all of it. They are read on this machine, so none
of those four things is ever the reason a transcript is late — and phase 6's
deferral, which is what a recording is actually waiting on, is a fact about the
build rather than about the queue and comes from the projection.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..extract import jobs as jobs_mod
from ..ingest.mime import is_speech
from ..projection import reading as reading_mod
from . import endpoint_state


def describe(
    state: reading_mod.Reading,
    queue: jobs_mod.Queue,
    endpoint: endpoint_state.EndpointState,
    mime: str = "",
) -> dict[str, Any]:
    """The projection's answer, plus the live reason where there is one.

    ``text`` is what a screen shows. ``recorded_text`` is what the wiki says,
    carried alongside so a reader comparing the two can see that the screen is
    adding a reason rather than saying something different.
    """
    result: dict[str, Any] = {
        "state": state.state,
        "text": state.sentence(),
        "recorded_text": state.sentence(),
        "claims": state.claims,
        "awaiting": state.awaiting,
        "finished": state.is_finished,
        "deferred": state.state == reading_mod.TRANSCRIBED,
    }
    if state.state != reading_mod.NOT_READ:
        return result

    job = queue.for_artifact(state.short)
    result["job"] = job.state if job is not None else None

    if is_speech(mime):
        # Waiting for the local speech model, which needs no box and no key. A
        # queue reason here would send someone to check their tailnet over a
        # transcript that is thirty seconds away.
        result["text"] = "Being written down on this computer."
        if job is not None and job.state in (jobs_mod.UNREADABLE, jobs_mod.NEEDS_ATTENTION):
            result["text"] = (
                f"Could not be written down — {job.reason}"
                if job.reason
                else "Could not be written down."
            )
        return result

    if job is not None and job.state == jobs_mod.BLOCKED_AUTH:
        result["text"] = (
            "Waiting to be read — the password for the computer that reads your "
            "files was refused."
        )
        return result
    if job is not None and job.state == jobs_mod.UNREADABLE:
        result["text"] = (
            f"Could not be read — {job.reason}" if job.reason else "Could not be read."
        )
        return result
    if job is not None and job.state == jobs_mod.NEEDS_ATTENTION:
        result["text"] = (
            f"Not read — {job.reason}" if job.reason else "Not read; it needs a look."
        )
        return result

    if endpoint.state == endpoint_state.UNAUTHORISED:
        result["text"] = (
            "Waiting to be read — the password for the computer that reads your "
            "files was refused."
        )
        return result
    if endpoint.state == endpoint_state.NOT_CONFIGURED:
        result["text"] = "Waiting to be read — nothing is set up to read your files yet."
        return result
    if endpoint.state in (endpoint_state.MISCONFIGURED, endpoint_state.BLIND):
        result["text"] = (
            "Waiting to be read — the computer that reads your files is not set up "
            "correctly."
        )
        return result
    if endpoint.state == endpoint_state.UNREACHABLE:
        result["text"] = (
            "Waiting to be read — the computer that reads your files is asleep."
        )
        return result

    ahead = _ahead_of(state.short, queue)
    if ahead == 1:
        result["text"] = "Waiting to be read — behind 1 other file."
    elif ahead > 1:
        result["text"] = f"Waiting to be read — behind {ahead} other files."
    else:
        result["text"] = "Being read now."
    return result


def _ahead_of(short: str, queue: jobs_mod.Queue) -> int:
    """How many runnable jobs are in front of this one.

    By job id, which is a ULID and so is creation order. Counting the whole
    queue instead would say "behind 9 other files" to the file at the front of
    it, which is the one about to be read.
    """
    jobs = sorted(queue.runnable(), key=lambda job: job.id)
    for position, job in enumerate(jobs):
        if job.artifact == short:
            return position
    return len(jobs)


def index(snapshot, queue: jobs_mod.Queue, endpoint) -> dict[str, dict[str, Any]]:
    """Every artefact's state, live, keyed by short hash."""
    readings = reading_mod.index(
        snapshot.events, snapshot.artifacts, snapshot.projection.artifacts
    )
    return {
        short: describe(
            state, queue, endpoint, getattr(snapshot.artifacts.get(short), "mime", "")
        )
        for short, state in readings.items()
    }


def for_row(row, live: Mapping[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The live state for a timeline row, or ``None`` if the row is not an artefact."""
    if not row.reading:
        return None
    return live.get(row.cite)
