"""The work queue, at ``.agent/jobs.jsonl``.

``CLAUDE.md``: "Background work uses a simple in-process worker with a JSONL job
file so it survives restart." No broker, no database — the same append-only
discipline as the event log, for the same reason: a file a person can read in
five years with nothing installed.

**A job's state is the fold of its lines, not the last line rewritten.** Every
transition appends; nothing is ever edited or deleted. A crash mid-write leaves a
torn final line, which is skipped and reported rather than repaired, and the next
append leads with a newline so it lands cleanly. This is the same rule the event
log follows and it exists for the same reason — capture must never fail because a
previous process died.

**Auth failure parks the whole queue, and is not a retry.** ``MODELS.md`` is
explicit that a 401 is terminal: "Do not retry with backoff. Retrying a rotated
key fifty times achieves nothing and may trip rate limiting or lockout on the
server." So the queue stops, every waiting job is marked ``blocked-auth``, and
the state is distinct from ``unreachable`` everywhere it is reported. Collapsing
the two is how a rotated key looks like a sleeping Mac and nobody investigates
for a week.

This file is inside the vault, so it is inside a folder that syncs. Nothing here
ever writes a credential into it — see the redaction filter — and the file is a
cache of work in progress rather than a source of truth: deleting it loses the
queue, not the record.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..events.envelope import format_ts, now_ts, parse_ts_or_none

log = logging.getLogger("agent.extract")

JOBS_FILENAME = "jobs.jsonl"

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
#: The box is asleep or off the tailnet. Retried silently, drained later.
UNREACHABLE = "unreachable"
#: The key was rejected. Needs a person; never retried.
BLOCKED_AUTH = "blocked-auth"
#: Attempts are exhausted, or the failure is not one retrying can fix.
NEEDS_ATTENTION = "needs-attention"
#: The model read the artefact and could not make anything of it. Not a failure
#: of this system — a legitimate answer that sends the artefact to a person.
UNREADABLE = "unreadable"

TERMINAL = frozenset({DONE, BLOCKED_AUTH, NEEDS_ATTENTION, UNREADABLE})
#: States a drain will pick up. `unreachable` is here because that is what
#: "drain later" means.
RUNNABLE = frozenset({QUEUED, RUNNING, UNREACHABLE})

#: Attempts before a job stops being retried. Small: a job that has failed five
#: times for a reason backoff can fix is a job whose reason backoff cannot fix.
MAX_ATTEMPTS = 5

#: Seconds. Doubling, capped, so a box that is down overnight is not hammered.
_BACKOFF = (30, 120, 600, 1800, 3600)


def backoff_seconds(attempts: int) -> int:
    return _BACKOFF[min(max(attempts, 1), len(_BACKOFF)) - 1]


@dataclass(frozen=True)
class Job:
    """One artefact waiting to be read, and how that has gone so far."""

    id: str
    artifact: str
    state: str = QUEUED
    attempts: int = 0
    created: str = ""
    updated: str = ""
    reason: str | None = None
    not_before: str | None = None
    #: The extraction key, once one is known. Recorded so a job whose work was
    #: already done can be closed without calling the model at all.
    key: dict[str, str] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def is_runnable(self) -> bool:
        return self.state in RUNNABLE

    def ready_at(self, moment: datetime) -> bool:
        """Whether the backoff on a failed job has elapsed."""
        if not self.not_before:
            return True
        parsed = parse_ts_or_none(self.not_before)
        return parsed is None or parsed <= moment

    def describe(self) -> str:
        detail = f" — {self.reason}" if self.reason else ""
        return f"{self.artifact}  {self.state}{detail}"

    def to_line(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "artifact": self.artifact,
            "state": self.state,
            "attempts": self.attempts,
            "created": self.created,
            "updated": self.updated,
            "reason": self.reason,
            "not_before": self.not_before,
            "key": self.key,
        }


def _from_line(data: Any, previous: Job | None) -> Job | None:
    """Fold one line onto what is known so far. Unknown fields are ignored."""
    if not isinstance(data, dict):
        return None
    job_id = data.get("id")
    artifact = data.get("artifact") or (previous.artifact if previous else None)
    if not isinstance(job_id, str) or not isinstance(artifact, str):
        return None
    base = previous or Job(id=job_id, artifact=artifact)
    return Job(
        id=job_id,
        artifact=artifact,
        state=data.get("state", base.state),
        attempts=data.get("attempts", base.attempts),
        created=data.get("created") or base.created,
        updated=data.get("updated") or base.updated,
        reason=data.get("reason", base.reason),
        not_before=data.get("not_before", base.not_before),
        key=data.get("key", base.key),
    )


@dataclass
class Queue:
    """The job file, read as a fold and appended to a line at a time."""

    path: Path
    jobs: dict[str, Job] = field(default_factory=dict)
    #: Lines that could not be read. Reported, never rewritten — a torn line is
    #: what a crash mid-write looks like and it is not corruption to repair.
    malformed: tuple[str, ...] = ()

    @classmethod
    def open(cls, agent_dir: Path) -> Queue:
        path = Path(agent_dir) / JOBS_FILENAME
        queue = cls(path=path)
        queue.reload()
        return queue

    def reload(self) -> None:
        self.jobs = {}
        malformed: list[str] = []
        if not self.path.exists():
            return
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            log.warning("cannot read %s: %s", self.path, exc)
            return
        for number, line in enumerate(raw.split(b"\n"), start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                malformed.append(f"{self.path.name}:{number}: {exc}")
                continue
            folded = _from_line(data, self.jobs.get(data.get("id")))
            if folded is None:
                malformed.append(f"{self.path.name}:{number}: not a usable job record")
                continue
            self.jobs[folded.id] = folded
        self.malformed = tuple(malformed)

    # -- writing -----------------------------------------------------------

    def _append(self, job: Job) -> None:
        """One line, opened for append, with a leading newline after a torn write.

        Binary mode with an explicit ``\\n``: text mode would translate line
        endings on Windows and the file would differ between machines for no
        reason visible in it.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        needs_newline = False
        if self.path.exists() and self.path.stat().st_size:
            with self.path.open("rb") as handle:
                handle.seek(-1, os.SEEK_END)
                needs_newline = handle.read(1) != b"\n"
        payload = json.dumps(job.to_line(), sort_keys=True, ensure_ascii=False)
        with self.path.open("ab") as handle:
            if needs_newline:
                handle.write(b"\n")
            handle.write(payload.encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.jobs[job.id] = job

    def add(self, artifact: str, job_id: str | None = None, ts: str | None = None) -> Job:
        """Queue an artefact, or return the job already covering it.

        Idempotent on the artefact, not on the call: capturing the same
        photograph twice must not queue it twice, and the ingest layer has
        already decided that identical bytes are one artefact.
        """
        existing = self.for_artifact(artifact)
        if existing is not None and not existing.is_terminal:
            return existing
        moment = ts or now_ts()
        job = Job(
            id=job_id or f"job-{artifact}-{moment}",
            artifact=artifact,
            state=QUEUED,
            created=moment,
            updated=moment,
        )
        self._append(job)
        return job

    def update(self, job: Job, state: str, reason: str | None = None, **extra) -> Job:
        moment = extra.pop("ts", None) or now_ts()
        updated = Job(
            id=job.id,
            artifact=job.artifact,
            state=state,
            attempts=extra.pop("attempts", job.attempts),
            created=job.created,
            updated=moment,
            reason=reason,
            not_before=extra.pop("not_before", None),
            key=extra.pop("key", job.key),
        )
        self._append(updated)
        return updated

    def started(self, job: Job) -> Job:
        return self.update(job, RUNNING, attempts=job.attempts + 1)

    def finished(self, job: Job, key: dict[str, str] | None = None) -> Job:
        return self.update(job, DONE, key=key or job.key)

    def unreadable(self, job: Job, reason: str) -> Job:
        """The model read it and could not make anything of it.

        Terminal, and deliberately not ``needs-attention``: nothing is wrong with
        the system. The artefact goes to a person, which is the right outcome and
        the one the whole "could not read — review manually" path exists to
        produce.
        """
        return self.update(job, UNREADABLE, reason)

    def failed(self, job: Job, reason: str, moment: datetime | None = None) -> Job:
        """A transient failure. Backs off, or gives up after enough attempts."""
        if job.attempts >= MAX_ATTEMPTS:
            return self.update(
                job,
                NEEDS_ATTENTION,
                f"{reason} (gave up after {job.attempts} attempts)",
            )
        wait = backoff_seconds(job.attempts)
        when = (moment or datetime.now(timezone.utc)).timestamp() + wait
        return self.update(
            job,
            UNREACHABLE,
            reason,
            not_before=format_ts(datetime.fromtimestamp(when, tz=timezone.utc)),
        )

    def park_for_auth(self, reason: str) -> tuple[Job, ...]:
        """Stop everything. The key was rejected and a person has to act.

        Every runnable job is marked, not just the one that failed: the next job
        would fail the same way, and a queue that kept trying would turn one
        clear message into fifty and might trip lockout on the box.
        """
        parked = []
        for job in self.runnable():
            parked.append(self.update(job, BLOCKED_AUTH, reason))
        return tuple(parked)

    def resume(self, ts: str | None = None) -> tuple[Job, ...]:
        """Put parked jobs back in the queue, after the key has been fixed."""
        resumed = []
        for job in sorted(self.jobs.values(), key=lambda j: j.id):
            if job.state == BLOCKED_AUTH:
                resumed.append(
                    self.update(job, QUEUED, "the key was re-entered", ts=ts, attempts=0)
                )
        return tuple(resumed)

    # -- reading -----------------------------------------------------------

    def for_artifact(self, artifact: str) -> Job | None:
        found = [job for job in self.jobs.values() if job.artifact == artifact]
        return sorted(found, key=lambda j: (j.updated, j.id))[-1] if found else None

    def runnable(self) -> tuple[Job, ...]:
        return tuple(
            sorted(
                (job for job in self.jobs.values() if job.is_runnable),
                key=lambda j: (j.created, j.id),
            )
        )

    def ready(self, moment: datetime | None = None) -> tuple[Job, ...]:
        """Runnable jobs whose backoff has elapsed."""
        now = moment or datetime.now(timezone.utc)
        return tuple(job for job in self.runnable() if job.ready_at(now))

    def blocked(self) -> tuple[Job, ...]:
        return tuple(
            sorted(
                (job for job in self.jobs.values() if job.state == BLOCKED_AUTH),
                key=lambda j: j.id,
            )
        )

    def counts(self) -> dict[str, int]:
        counted: dict[str, int] = {}
        for job in self.jobs.values():
            counted[job.state] = counted.get(job.state, 0) + 1
        return counted

    @property
    def is_parked(self) -> bool:
        return any(job.state == BLOCKED_AUTH for job in self.jobs.values())

    def depth(self) -> int:
        """What ``/api/health`` will report in phase 5."""
        return len(self.runnable())

    def __iter__(self) -> Iterator[Job]:
        return iter(sorted(self.jobs.values(), key=lambda j: (j.created, j.id)))


def describe(jobs: Iterable[Job]) -> Sequence[str]:
    return [job.describe() for job in jobs]
