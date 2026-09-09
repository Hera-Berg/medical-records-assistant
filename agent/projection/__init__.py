"""The projection engine: events in, wiki out.

Invariant 1 of the project is that the event log is the only source of truth and
everything under ``wiki/`` is derived from it. That is only true if this module
is a **pure function**: given the same events and the same ``as_of``, it produces
the same bytes on any machine, in any timezone, under any locale, in any order
the shards happen to be read.

``as_of`` is an explicit argument for that reason. Staleness and the
seven-day auto-apply both depend on the current date, so a clock read inside the
projection would make two rebuilds of an unchanged log disagree. The caller
supplies the moment; the CLI defaults it to now.

Nothing here mutates the log. A model proposes, a user decides, and this reduces
what they said into a record — the wiki is never written by an LLM and never
patched by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..events.envelope import Event, format_ts, parse_ts_or_none
from . import citations as citations_mod
from . import entities as entities_mod
from . import pages, reconcile, timeline, views, writer
from .claims import ClaimProblem
from .entities import Entity
from .reconcile import Reconciliation, ReviewItem
from .subjects import WIKI_DIRNAME

TIMELINE_DIRNAME = "timeline"


@dataclass(frozen=True)
class Projection:
    """The whole derived record, in memory, before anything is written."""

    as_of: datetime
    entities: Mapping[str, Entity] = field(default_factory=dict)
    rows: tuple[timeline.Row, ...] = ()
    files: Mapping[str, bytes] = field(default_factory=dict)
    review: tuple[ReviewItem, ...] = ()
    problems: tuple[ClaimProblem, ...] = ()
    anomalies: tuple[str, ...] = ()
    unresolved_citations: tuple[str, ...] = ()
    reconciliation: Reconciliation | None = None

    @property
    def current_medications(self) -> tuple[views.MedicationRow, ...]:
        """The generated view. Never written to the vault — see :mod:`.views`."""
        return views.current_medications(self.entities)

    @property
    def artifacts(self) -> Mapping[str, reconcile.ArtifactReview]:
        """How far each artefact's claims have been decided.

        Phase 7 reads this to keep an artefact whose claims were all rejected out
        of the inbox: it was reviewed, not left unprocessed.
        """
        if self.reconciliation is None:
            return {}
        return self.reconciliation.artifacts

    @property
    def review_by_tier(self) -> dict[str, tuple[ReviewItem, ...]]:
        grouped: dict[str, list[ReviewItem]] = {}
        for item in self.review:
            grouped.setdefault(item.consequence, []).append(item)
        return {tier: tuple(items) for tier, items in grouped.items()}

    def stats(self) -> dict[str, Any]:
        return {
            "entities": len(self.entities),
            "files": len(self.files),
            "timeline_rows": len(self.rows),
            "review": len(self.review),
            "conflicts": sum(
                1 for entity in self.entities.values() for _ in entity.conflicts
            ),
            "unreadable_claims": len(self.problems),
            "artifacts_reviewed": sum(
                1 for review in self.artifacts.values() if review.is_reviewed
            ),
            "artifacts_awaiting_review": sum(
                1 for review in self.artifacts.values() if not review.is_reviewed
            ),
        }


def parse_as_of(as_of: datetime | str | None) -> datetime | None:
    """A CLI ``--as-of`` string as a moment, or ``None`` to mean now.

    The projection defaults ``as_of`` to now only at this boundary, which is what
    keeps the reduction itself free of any clock read.
    """
    return None if as_of is None else _as_datetime(as_of)


def _as_datetime(as_of: datetime | str | None) -> datetime:
    if as_of is None:
        return datetime.now(timezone.utc)
    if isinstance(as_of, str):
        parsed = parse_ts_or_none(as_of)
        if parsed is None:
            raise ValueError(f"as_of {as_of!r} is not a timestamp")
        return parsed
    if as_of.tzinfo is None:
        return as_of.replace(tzinfo=timezone.utc)
    return as_of.astimezone(timezone.utc)


def project(events: Iterable[Event], as_of: datetime | str | None = None) -> Projection:
    """Reduce the event log into the derived record.

    *events* must already be in the log's ``(ts, id)`` order — that is what
    :func:`agent.events.log.read_all` returns, and the ordering is what makes the
    result independent of which device wrote what.
    """
    moment = _as_datetime(as_of)
    events = list(events)

    reconciliation = reconcile.reconcile(events, moment)
    entities = entities_mod.build_all(reconciliation, moment)
    # Some review items can only be known once an entity is assembled — a stop
    # the tier rule declined to act on is a fact about a medication's lifecycle,
    # not about a single slot. Rule 3 requires them in the queue as well as on
    # the page, so they are merged back in here and sorted with the rest.
    review = tuple(
        sorted(
            reconciliation.review + entities_mod.derived_review(entities),
            key=lambda item: item.sort_key,
        )
    )
    artifacts = citations_mod.index_artifacts(events)
    citer = citations_mod.Citer(artifacts, events)

    names = {subject_id: entity.name for subject_id, entity in entities.items()}
    rows = timeline.build(events, reconciliation, names)

    files: dict[str, bytes] = {}
    for subject_id in sorted(entities):
        entity = entities[subject_id]
        files[entity.rel_path] = pages.entity_page(entity, citer, moment.date())

    for month, month_rows in sorted(timeline.by_month(rows).items()):
        rel = f"{WIKI_DIRNAME}/{TIMELINE_DIRNAME}/{month}.md"
        files[rel] = pages.timeline_page(month, month_rows, citer)

    return Projection(
        as_of=moment,
        entities=entities,
        rows=rows,
        files=files,
        review=review,
        problems=reconciliation.problems,
        anomalies=reconciliation.anomalies,
        unresolved_citations=tuple(sorted(citer.unresolved)),
        reconciliation=reconciliation,
    )


@dataclass(frozen=True)
class RebuildReport:
    """What a rebuild produced, for the CLI and later for ``POST /api/rebuild``."""

    projection: Projection
    write: writer.WriteReport
    root: Path

    @property
    def problems(self) -> tuple[str, ...]:
        notes = list(self.write.problems)
        for problem in self.projection.problems:
            notes.append(f"claim not readable: {problem.describe()}")
        for key in self.projection.unresolved_citations:
            notes.append(
                f"citation {key} does not resolve to anything in the record; the wiki "
                f"says so rather than dropping the reference"
            )
        return tuple(notes)

    def to_dict(self) -> dict[str, Any]:
        projection = self.projection
        return {
            "root": str(self.root),
            "as_of": format_ts(projection.as_of),
            "stats": projection.stats(),
            "written": list(self.write.written),
            "unchanged": list(self.write.unchanged),
            "removed": list(self.write.removed),
            "foreign": list(self.write.foreign),
            "modified": list(self.write.modified),
            "manifest_missing": self.write.manifest_missing,
            "review": [
                {
                    "kind": item.kind,
                    "consequence": item.consequence,
                    "subject": item.subject_id,
                    "predicate": item.predicate,
                    "summary": item.summary,
                }
                for item in projection.review
            ],
            "anomalies": list(projection.anomalies),
            "problems": list(self.problems),
            "current_medications": [row.to_dict() for row in projection.current_medications],
        }


def rebuild(vault, as_of: datetime | str | None = None) -> RebuildReport:
    """Replay the log into ``wiki/`` and record what was written.

    The wiki is regenerated wholesale, but only files this code wrote before are
    ever removed — see :mod:`.writer`. A rebuild is safe to run at any time and
    changes nothing in ``events/`` or ``raw/``.
    """
    read = vault.read()
    projection = project(read.events, as_of)
    report = writer.write(
        vault.root,
        projection.files,
        as_of=format_ts(projection.as_of),
        stats=projection.stats(),
    )
    return RebuildReport(projection=projection, write=report, root=vault.root)


__all__ = [
    "Projection",
    "RebuildReport",
    "project",
    "rebuild",
    "views",
]
