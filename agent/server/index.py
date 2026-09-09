"""``.agent/index.sqlite`` — a cache, and never the only home of any fact.

``CLAUDE.md`` is unambiguous about this file: "SQLite at ``.agent/index.sqlite``,
treated as a disposable cache. Never the only home of any fact." So the design
constraint is not "make queries fast", it is **make deleting this file
unobservable**. Every question this module answers can be answered from the
in-memory projection, and every caller does exactly that when the index declines
to answer. There is a test that deletes the file between two requests and asserts
the responses are byte-identical.

Three things follow from that.

The index is **derived wholesale, never patched.** It is rebuilt from a snapshot
in one transaction whenever the snapshot's fingerprint stops matching the
generation recorded in it. Incremental maintenance would create a second
reduction of the event log alongside :mod:`agent.projection`, and the moment two
reductions exist one of them is wrong.

Every failure is a **miss, not an error.** A corrupt file, a read-only vault, a
sync client mid-write, a SQLite build without the features we want: all of them
mean "ask the projection instead". Nothing in the record depends on this working
and so nothing here may take a request down.

It is **excluded from byte-identical rebuild** — see
``agent.vault.NON_DETERMINISTIC_DERIVED``. SQLite files carry page-level state
that differs between two runs producing identical logical content, and this holds
no fact that ``wiki/`` does not. Excluding it narrows nothing: the same test
deletes it and rebuilds, and the wiki bytes still have to match.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..projection import dates as dates_mod
from .state import Snapshot

INDEX_FILENAME = "index.sqlite"

#: Bumped when the shape below changes. A file written by an older version is
#: discarded and rebuilt rather than migrated — it is a cache, and a migration
#: path for a cache is code that can only ever introduce bugs.
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE artifacts (
    short        TEXT PRIMARY KEY,
    digest       TEXT NOT NULL,
    rel          TEXT NOT NULL,
    mime         TEXT NOT NULL,
    ingested_ts  TEXT,
    captured_ts  TEXT,
    artifact_ts  TEXT,
    source       TEXT,
    reseen       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX artifacts_digest ON artifacts(digest);

CREATE TABLE entities (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    slug           TEXT NOT NULL,
    name           TEXT NOT NULL,
    status         TEXT NOT NULL,
    evidence_tier  TEXT,
    stale          INTEGER NOT NULL DEFAULT 0,
    conflicted     INTEGER NOT NULL DEFAULT 0,
    is_stub        INTEGER NOT NULL DEFAULT 0,
    last_confirmed TEXT
);
CREATE INDEX entities_kind ON entities(kind, name);

CREATE TABLE rows (
    ord         INTEGER PRIMARY KEY,
    event_id    TEXT NOT NULL,
    subject_id  TEXT,
    marker      TEXT NOT NULL,
    date_kind   TEXT NOT NULL,
    band_start  TEXT NOT NULL,
    band_end    TEXT NOT NULL
);
CREATE INDEX rows_band ON rows(band_start, band_end);
CREATE INDEX rows_subject ON rows(subject_id);
"""


def generation_of(snapshot: Snapshot) -> str:
    """A short signature of the log state a snapshot was built from."""
    digest = hashlib.sha256()
    for name, size, mtime in snapshot.fingerprint:
        digest.update(f"{name}:{size}:{mtime}\n".encode("utf-8"))
    digest.update(snapshot.as_of.date().isoformat().encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True)
class RowKey:
    """The identity of one timeline row, as the index knows it.

    Only enough to pick rows out of the snapshot by position. The row's text,
    citation and rendered date stay in the projection, so there is exactly one
    place that decides how a row reads.
    """

    ord: int
    event_id: str


class Index:
    """The cache file, or a stand-in for it that always misses."""

    def __init__(self, path: Path):
        self.path = path
        #: Set when SQLite cannot be used at all here. Sticky for the process:
        #: a vault on a read-only mount will not become writable mid-session,
        #: and retrying per request would cost a failed open every time.
        self.unavailable = False
        self._generation: str | None = None

    @classmethod
    def open(cls, agent_dir: Path) -> Index:
        return cls(Path(agent_dir) / INDEX_FILENAME)

    # -- lifecycle ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection | None:
        if self.unavailable:
            return None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=5.0)
            connection.row_factory = sqlite3.Row
            return connection
        except (sqlite3.Error, OSError):
            self.unavailable = True
            return None

    def ensure(self, snapshot: Snapshot) -> bool:
        """Make the file describe *snapshot*. ``False`` if it cannot be used."""
        wanted = generation_of(snapshot)
        if self._generation == wanted and self._matches(wanted):
            return True
        return self.refresh(snapshot)

    def _matches(self, generation: str) -> bool:
        connection = self._connect()
        if connection is None:
            return False
        try:
            with closing(connection):
                found = dict(
                    connection.execute("SELECT key, value FROM meta").fetchall()
                )
        except sqlite3.Error:
            return False
        return (
            found.get("generation") == generation
            and found.get("schema") == str(SCHEMA_VERSION)
        )

    def refresh(self, snapshot: Snapshot) -> bool:
        """Rebuild the whole file from *snapshot*, in one transaction."""
        connection = self._connect()
        if connection is None:
            return False
        generation = generation_of(snapshot)
        try:
            with closing(connection):
                with connection:
                    for table in ("meta", "artifacts", "entities", "rows"):
                        connection.execute(f"DROP TABLE IF EXISTS {table}")
                    connection.executescript(_SCHEMA)
                    connection.executemany(
                        "INSERT INTO meta (key, value) VALUES (?, ?)",
                        [
                            ("schema", str(SCHEMA_VERSION)),
                            ("generation", generation),
                            ("built", snapshot.built_ts),
                        ],
                    )
                    connection.executemany(
                        "INSERT INTO artifacts (short, digest, rel, mime, ingested_ts,"
                        " captured_ts, artifact_ts, source, reseen)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        list(_artifact_rows(snapshot)),
                    )
                    connection.executemany(
                        "INSERT INTO entities (id, kind, slug, name, status,"
                        " evidence_tier, stale, conflicted, is_stub, last_confirmed)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        list(_entity_rows(snapshot)),
                    )
                    connection.executemany(
                        "INSERT INTO rows (ord, event_id, subject_id, marker,"
                        " date_kind, band_start, band_end) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        list(_timeline_rows(snapshot)),
                    )
        except (sqlite3.Error, OSError):
            # A cache that cannot be written is not a fault to report. The
            # projection answers everything regardless.
            self._generation = None
            return False
        self._generation = generation
        return True

    def discard(self) -> None:
        """Delete the file. Loses no fact; the next request rebuilds it."""
        self._generation = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    # -- queries -----------------------------------------------------------
    #
    # Each returns ``None`` to mean "I cannot answer this", which every caller
    # reads as "ask the projection". None of them ever raise.

    def _query(self, sql: str, params: Sequence[Any]) -> list[sqlite3.Row] | None:
        connection = self._connect()
        if connection is None:
            return None
        try:
            with closing(connection):
                return list(connection.execute(sql, tuple(params)).fetchall())
        except sqlite3.Error:
            return None

    def shorts_matching(self, prefix: str) -> list[str] | None:
        """Every artefact hash beginning with *prefix*, or matching a digest."""
        found = self._query(
            "SELECT short FROM artifacts WHERE short LIKE ? ESCAPE '\\'"
            " OR digest = ? ORDER BY short",
            (_like_prefix(prefix), prefix),
        )
        return None if found is None else [row["short"] for row in found]

    def row_keys(
        self,
        start: str | None = None,
        end: str | None = None,
        subject_id: str | None = None,
        markers: Sequence[str] = (),
    ) -> list[RowKey] | None:
        """Timeline rows overlapping a date range, in the projection's order.

        Overlap rather than containment: a row dated "around June 2026 (±15
        days)" belongs in a June query, and testing only its point date would
        drop exactly the rows whose dating is least certain — the ones a reader
        most needs to see when they go looking.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if start:
            clauses.append("band_end >= ?")
            params.append(start)
        if end:
            clauses.append("band_start <= ?")
            params.append(end)
        if subject_id:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        if markers:
            clauses.append(f"marker IN ({','.join('?' * len(markers))})")
            params.extend(markers)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        found = self._query(
            f"SELECT ord, event_id FROM rows{where} ORDER BY ord", params
        )
        if found is None:
            return None
        return [RowKey(ord=row["ord"], event_id=row["event_id"]) for row in found]


def _like_prefix(prefix: str) -> str:
    """A ``LIKE`` pattern matching *prefix* literally, wildcards escaped."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


def _artifact_rows(snapshot: Snapshot) -> Iterable[tuple[Any, ...]]:
    for short in sorted(snapshot.artifacts):
        artifact = snapshot.artifacts[short]
        yield (
            artifact.short,
            artifact.digest,
            artifact.rel,
            artifact.mime,
            artifact.ingested_ts,
            artifact.captured_ts,
            artifact.artifact_ts,
            artifact.source,
            artifact.reseen_count,
        )


def _entity_rows(snapshot: Snapshot) -> Iterable[tuple[Any, ...]]:
    entities = snapshot.projection.entities
    for subject_id in sorted(entities):
        entity = entities[subject_id]
        yield (
            entity.id,
            entity.subject.kind,
            entity.subject.slug,
            entity.name,
            entity.status,
            entity.evidence_tier,
            int(entity.stale),
            int(bool(entity.conflicts)),
            int(entity.is_stub),
            entity.last_confirmed.iso if entity.last_confirmed else None,
        )


def _timeline_rows(snapshot: Snapshot) -> Iterable[tuple[Any, ...]]:
    for position, row in enumerate(snapshot.projection.rows):
        start, end = row.date.band
        yield (
            position,
            row.event_id,
            row.subject_id,
            row.marker,
            row.date_kind,
            start.isoformat(),
            end.isoformat(),
        )


def in_range(row, start: str | None, end: str | None) -> bool:
    """The fallback the index accelerates. Same overlap rule, no SQLite."""
    band_start, band_end = row.date.band
    if start:
        parsed = dates_mod.parse_iso_date(start)
        if parsed is not None and band_end < parsed:
            return False
    if end:
        parsed = dates_mod.parse_iso_date(end)
        if parsed is not None and band_start > parsed:
            return False
    return True
