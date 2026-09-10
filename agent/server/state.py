"""The server's view of one vault: a cached projection, and one writer at a time.

Two problems this solves.

**Serving must not write.** ``wiki/`` is written by exactly one thing — a
rebuild, whether from the CLI or from ``POST /api/rebuild``. Every read route
answers from :func:`agent.projection.project`, which is the same pure reduction
with nothing written to disk. A ``GET`` that rewrote the wiki would make the
folder churn every time a page was opened, and would put a clock read on the
path of every request.

**The projection is pure, so it can be cached, and ``as_of`` is why it expires.**
Given the same events and the same ``as_of`` it produces the same result, so a
snapshot stays good until one of those changes. The log is watched by
fingerprint — every shard's name, size and mtime — which is a handful of
``stat`` calls rather than a re-read. ``as_of`` is pinned to the moment the
snapshot was built and expires when the UTC date rolls over, because staleness
and the seven-day review window are both measured in days: recomputing per
request would put a clock read back in, and never recomputing would leave a
medication reading "confirmed 7 months ago" into its ninth month.

**One writer at a time.** :func:`agent.events.log.append` checks the last byte
of a shard before appending, so two uploads landing together can interleave that
check with each other's write. A browser dropping five files at once does
exactly that. One lock across every append and every rebuild removes the race;
contention is not a concern at one user and one process.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import config as config_mod
from ..events.envelope import Event, format_ts
from ..events.log import LogRead
from ..extract import jobs as jobs_mod
from ..projection import Projection, project
from ..projection import citations as citations_mod
from ..projection.citations import Artifact, Citer
from . import endpoint_state
from .endpoint_state import EndpointState


def under_pytest() -> bool:
    """Whether this process is a test run.

    Used for exactly one thing: keeping the background worker switched off by
    default under tests, so that no test can come to depend on when a daemon
    thread happened to get scheduled. A test that wants the worker's behaviour
    drives the drain directly instead.
    """
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


@dataclass(frozen=True)
class Snapshot:
    """One consistent read of the whole record, and what was derived from it."""

    events: tuple[Event, ...]
    read: LogRead
    projection: Projection
    artifacts: dict[str, Artifact]
    citer: Citer
    fingerprint: tuple[tuple[str, int, int], ...]
    as_of: datetime
    built_ts: str

    @property
    def anomaly_count(self) -> int:
        """Anomalies from the log and from the projection, counted together.

        ``CLAUDE.md``: anomalies "must be surfaced: ``/api/health`` in phase 5
        and the review inbox in phase 7 both report the count, so they cannot
        scroll past unseen." The two sources are different kinds of thing — a
        shard naming violation against a claim payload disagreeing with its
        computed consequence tier — but a count that omitted either would let
        one of them scroll past, which is the outcome the rule forbids.
        """
        return len(self.projection.anomalies) + len(self.read.anomalies)

    def anomalies(self) -> tuple[str, ...]:
        return tuple(self.projection.anomalies) + tuple(self.read.anomalies)


def fingerprint_log(events_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """A cheap signature of the event log's on-disk state.

    Name, size and mtime of every file in ``events/`` — not just well-named
    shards, because a conflicted copy appearing is itself a change worth
    noticing. Deliberately not a content hash: this runs on every request and
    the log grows for a lifetime.
    """
    if not events_dir.is_dir():
        return ()
    found: list[tuple[str, int, int]] = []
    for path in sorted(events_dir.iterdir()):
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        found.append((path.name, stat.st_size, stat.st_mtime_ns))
    return tuple(found)


class RecordState:
    """Everything the routes share: the vault, the cached projection, the lock."""

    def __init__(self, vault, clock: Callable[[], datetime] | None = None):
        self.vault = vault
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        #: Held across every append and every rebuild. Re-entrant so a rebuild
        #: can take a snapshot while holding it.
        self.lock = threading.RLock()
        self._snapshot: Snapshot | None = None
        self._endpoint = EndpointState()
        self._endpoint_lock = threading.Lock()

    # -- the record --------------------------------------------------------

    def now(self) -> datetime:
        return self._clock()

    def snapshot(self) -> Snapshot:
        """The current projection, rebuilt only when it can no longer be right."""
        with self.lock:
            current = self._snapshot
            if current is not None and not self._is_stale(current):
                return current
            fresh = self._build()
            self._snapshot = fresh
            return fresh

    def invalidate(self) -> None:
        """Drop the cached projection. Called after anything appends."""
        with self.lock:
            self._snapshot = None

    def _is_stale(self, snapshot: Snapshot) -> bool:
        if snapshot.fingerprint != fingerprint_log(self.vault.events_dir):
            return True
        # Staleness and the review window are counted in days, so a snapshot
        # stops being right when the date moves, not when the second does.
        return snapshot.as_of.date() != self.now().date()

    def _build(self) -> Snapshot:
        moment = self.now()
        fingerprint = fingerprint_log(self.vault.events_dir)
        read = self.vault.read()
        events = tuple(read.events)
        projection = project(events, moment)
        artifacts = citations_mod.index_artifacts(events)
        return Snapshot(
            events=events,
            read=read,
            projection=projection,
            artifacts=artifacts,
            # The routes resolve footnotes exactly as the wiki does, so a
            # citation on screen and the same citation in the folder say the
            # same words about the same artefact.
            citer=Citer(artifacts, events),
            fingerprint=fingerprint,
            as_of=moment,
            built_ts=format_ts(moment),
        )

    def reload_config(self) -> None:
        """Re-read ``config.toml`` after it was changed on disk.

        The profile it carries decides how the log is appended and how the scan
        reports forks, so a change to it invalidates the cached projection as
        surely as an appended event does.
        """
        with self.lock:
            self.vault.config = config_mod.load(
                self.vault.root / config_mod.CONFIG_FILENAME
            )
            self._snapshot = None

    # -- the work queue ----------------------------------------------------

    def queue(self) -> jobs_mod.Queue:
        """The job queue, re-read from disk.

        Read fresh rather than held: the CLI may be draining it in another
        process while this one serves, and a queue depth that disagreed with
        ``.agent/jobs.jsonl`` would be worse than one that costs a small file
        read to produce.
        """
        return jobs_mod.Queue.open(self.vault.root / ".agent")

    # -- the inference box -------------------------------------------------

    @property
    def endpoint(self) -> EndpointState:
        with self._endpoint_lock:
            return self._endpoint

    def set_endpoint(self, state: EndpointState) -> None:
        with self._endpoint_lock:
            self._endpoint = state

    def record_endpoint_error(self, exc: BaseException) -> None:
        self.set_endpoint(endpoint_state.from_error(exc, format_ts(self.now())))

    def record_probe(self, report) -> None:
        self.set_endpoint(endpoint_state.from_probe(report, format_ts(self.now())))

    # -- writing -----------------------------------------------------------

    def append(self, event: Event) -> Path:
        """Append one event under the lock, and drop the cached projection."""
        with self.lock:
            path = self.vault.append(event)
            self._snapshot = None
            return path

    def health_summary(self) -> dict[str, Any]:
        """The parts of ``/api/health`` that come from the record itself."""
        snapshot = self.snapshot()
        stats = snapshot.projection.stats()
        return {
            "events": len(snapshot.events),
            "artifacts": len(snapshot.artifacts),
            "entities": stats["entities"],
            "review": stats["review"],
            "conflicts": stats["conflicts"],
            "anomalies": snapshot.anomaly_count,
            "as_of": snapshot.built_ts,
        }
