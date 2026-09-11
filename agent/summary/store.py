"""Preparing a sheet, writing it into ``exports/``, and reading it back later.

The log is the source of truth here as everywhere else. Preparing a summary
appends one ``summary.generated`` event recording what the sheet was built from
— the question the patient typed, the reference point, ``as_of``, and the claim
events the rows rested on — and *then* writes the two export files. The event
comes first on purpose: the files are derived and can be written again, and an
export with no event behind it is a document the record cannot explain.

**Re-rendering projects the log truncated at the event.** The projection is a
pure function of ``(events, as_of)``, so pinning both reproduces the sheet
exactly — a correction made in October does not silently rewrite the page handed
over in June. That is what makes an export a document rather than a view.

**A rejection withdraws a stored sheet rather than re-rendering it.** Rejected
content never renders, "not in entity pages, not in exports", and a sheet
prepared before a rejection cites something the user has since said is not true
of them. It cannot be un-printed, but it can stop being served: the claim ids
every row rested on are in the event, and if any of them has since been
rejected, :func:`restore` returns the sheet withdrawn and the caller offers a
fresh one. Nothing about the withdrawn content is reproduced in saying so.

**The before-picture is built without anything since rejected.** Otherwise the
"was" column would re-assert retracted content in the one place a clinician
reads it. Dropping those claims from the truncated stream means such a change
reads as newly added rather than as changed — slightly wrong about history, in
the direction that cannot print something the user retracted.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..errors import HealthAgentError
from ..events.envelope import Event, format_ts, parse_ts_or_none
from ..events import envelope
from ..projection import dates as dates_mod
from ..projection import project
from ..projection import citations as citations_mod
from ..projection.citations import Citer
from ..projection.subjects import slugify
from . import DEFAULT_WINDOW_DAYS, PREVIEW_ID, build, html as html_mod, markdown as md_mod
from .model import Summary

EVENT_TYPE = "summary.generated"

#: Where exports live, relative to the vault root. Named in the storage layout.
EXPORTS_DIRNAME = "exports"

#: Mode on a written export. The same reasoning as the sidecars: a guardrail
#: against accident, never a security control — the folder syncs to third-party
#: storage and the directory permissions still allow deletion.
EXPORT_MODE = 0o600

#: Used when the visit label yields no usable slug. A filename has to be
#: something, and "visit" says what the file is without pretending to know what
#: it was for.
DEFAULT_SLUG = "visit"

#: How the reference point for "what changed" was arrived at. Recorded in the
#: event so a sheet re-rendered in five years measures from the same moment, and
#: printed on the sheet so the reader can see what "changed" was measured
#: against.
FROM_PREVIOUS = "previous-summary"
FROM_CHOSEN = "chosen"
FROM_WINDOW = "default-window"

_NOTES = {
    FROM_PREVIOUS: "Measured against my previous summary, prepared {when}.",
    FROM_CHOSEN: "Measured from {when}, a date I chose.",
    FROM_WINDOW: (
        "I have no earlier summary, so this covers everything since {when} — "
        "the last three months."
    ),
}


class SummaryError(HealthAgentError):
    """Something about a summary could not be done, said in a full sentence."""


@dataclass(frozen=True)
class Reference:
    """The moment "what changed" is measured against, and how it was chosen."""

    ts: str | None
    day: date | None
    source: str

    @property
    def reason(self) -> str:
        """The heading's date — "4 June 2026"."""
        return dates_mod.render_date(self.day) if self.day is not None else ""

    @property
    def note(self) -> str:
        if self.day is None:
            return ""
        return _NOTES[self.source].format(when=dates_mod.render_date(self.day))


@dataclass(frozen=True)
class Prepared:
    """A sheet that has been written: the event, the summary, and the files."""

    event: Event
    summary: Summary
    #: Vault-relative path -> bytes, in the order they were written.
    files: dict[str, bytes]

    @property
    def markdown_path(self) -> str:
        return next(p for p in self.files if p.endswith(".md"))

    @property
    def html_path(self) -> str:
        return next(p for p in self.files if p.endswith(".html"))


@dataclass(frozen=True)
class Restored:
    """A stored sheet read back out of the log.

    ``summary`` is ``None`` when the sheet has been withdrawn — a claim it
    rested on has since been rejected — and ``withdrawn`` names how many, never
    what they said.
    """

    event: Event
    summary: Summary | None
    withdrawn: tuple[str, ...] = ()
    exports: dict[str, str] = dataclasses.field(default_factory=dict)

    @property
    def is_withdrawn(self) -> bool:
        return bool(self.withdrawn)

    @property
    def explanation(self) -> str:
        """Why this sheet is not being shown, with none of it reproduced."""
        count = len(self.withdrawn)
        entry = "entry" if count == 1 else "entries"
        return (
            f"This summary is no longer shown. Since it was prepared on "
            f"{_prepared_words(self.event)}, you rejected {count} {entry} it was "
            f"built from, and a rejected entry is not printed anywhere — not on a "
            f"page, not in an export. Prepare a new summary to get a current one. "
            f"The file that was written at the time is still in your exports "
            f"folder; delete it from the Files screen if you do not want it kept."
        )


def _prepared_words(event: Event) -> str:
    day = dates_mod.parse_iso_date(str(event.payload.get("prepared") or ""))
    return dates_mod.render_date(day) if day else event.ts


# -- reading the log --------------------------------------------------------


def summaries(events: Iterable[Event]) -> tuple[Event, ...]:
    """Every summary ever prepared, oldest first, in the log's own order."""
    return tuple(event for event in events if event.type == EVENT_TYPE)


def rejected_claims(events: Iterable[Event]) -> frozenset[str]:
    """Claim events whose latest user decision was a rejection.

    Latest, not any: a claim rejected and later confirmed again is back in the
    record, and a sheet citing it is not withdrawn. This mirrors the rule
    reconciliation applies — the most recent decision governs among acts of
    equal authority.
    """
    latest: dict[str, Event] = {}
    for event in events:
        if event.type not in ("claim.confirmed", "claim.rejected", "claim.corrected"):
            continue
        target = event.payload.get("target")
        if not isinstance(target, str) or not target:
            continue
        current = latest.get(target)
        if current is None or event.sort_key > current.sort_key:
            latest[target] = event
    return frozenset(
        target for target, event in latest.items() if event.type == "claim.rejected"
    )


def reference_for(
    events: Sequence[Event], as_of: datetime, since: date | str | None = None
) -> Reference:
    """When "since last visit" means, and how that was decided.

    A date the user typed wins. Otherwise the previous summary is the last
    visit, which is the whole idea: the sheet is prepared for an appointment, so
    the last one prepared marks the last appointment. With neither, a stated
    three-month window — a section reporting changes over an unnamed period is
    asserting a window it never disclosed.
    """
    if since is not None:
        day = since if isinstance(since, date) else dates_mod.parse_iso_date(since)
        if day is None:
            raise SummaryError(
                f"{since!r} is not a date this can measure from; write it as "
                f"YYYY-MM-DD, or leave it out to measure from your last summary."
            )
        # End of the chosen day: "since 4 June" means changes after the fourth,
        # not changes after midnight at its start.
        return Reference(
            ts=f"{day.isoformat()}T23:59:59Z", day=day, source=FROM_CHOSEN
        )

    previous = summaries(events)
    if previous:
        last = previous[-1]
        parsed = parse_ts_or_none(last.ts)
        return Reference(
            ts=last.ts,
            day=parsed.date() if parsed else None,
            source=FROM_PREVIOUS,
        )

    moment = as_of - timedelta(days=DEFAULT_WINDOW_DAYS)
    return Reference(ts=format_ts(moment), day=moment.date(), source=FROM_WINDOW)


# -- composing --------------------------------------------------------------


def compose(
    events: Sequence[Event],
    *,
    as_of: datetime,
    question: str = "",
    label: str = "",
    since: date | str | None = None,
    reference: Reference | None = None,
    demo: bool = False,
    summary_id: str = PREVIEW_ID,
) -> Summary:
    """Build one sheet from an event stream. Writes nothing, appends nothing.

    This is the whole of the summary's dependence on the outside world: a list
    of events and a moment. Given the same two it produces the same sheet on any
    machine, which is what makes a stored summary re-renderable rather than
    merely re-derivable into something similar.
    """
    events = list(events)
    reference = reference or reference_for(events, as_of, since)

    now = project(events, as_of)
    before = None
    if reference.ts is not None:
        before_at = parse_ts_or_none(reference.ts)
        if before_at is not None:
            before = project(_as_at(events, before_at), before_at).entities

    artifacts = citations_mod.index_artifacts(events)
    return build(
        now.entities,
        before=before,
        review=now.review,
        citer=Citer(artifacts, events),
        prepared=as_of.date(),
        question=question,
        label=label,
        since=reference.day,
        since_ts=reference.ts,
        since_reason=reference.reason,
        since_note=reference.note,
        demo=demo,
        summary_id=summary_id,
    )


def _as_at(events: Sequence[Event], moment: datetime) -> list[Event]:
    """The log as it stood at *moment*, minus anything since rejected.

    Two filters with different reasons. The timestamp is what makes "before"
    mean before. The rejection filter is what stops a "was" value on the sheet
    re-asserting content the user has retracted — see the module docstring. A
    confirmation left pointing at a dropped claim raises an anomaly inside this
    throwaway projection and nothing reads those; the entities are all that is
    wanted from it.
    """
    rejected = rejected_claims(events)
    kept: list[Event] = []
    for event in events:
        instant = event.instant
        if instant is not None and instant > moment:
            continue
        if event.type in ("claim.proposed", "claim.corrected") and event.id in rejected:
            continue
        kept.append(event)
    return kept


# -- preparing --------------------------------------------------------------


def plan(
    events: Sequence[Event],
    *,
    device: str,
    as_of: datetime,
    root: Path,
    question: str = "",
    label: str = "",
    since: date | str | None = None,
    demo: bool = False,
) -> Prepared:
    """Everything a prepared summary consists of, before anything is written.

    Built in one piece so the caller can append the event and write the files
    under one lock. The event id is minted first and becomes the summary's id,
    because the sheet prints it and the two must be the same string.
    """
    reference = reference_for(list(events), as_of, since)
    event = envelope.new(EVENT_TYPE, device=device, ts=format_ts(as_of))
    summary = compose(
        events,
        as_of=as_of,
        question=question,
        label=label,
        reference=reference,
        demo=demo,
        summary_id=event.id,
    )

    stem = export_stem(root, summary.prepared, label)
    files = {
        f"{EXPORTS_DIRNAME}/{stem}.md": md_mod.render(summary),
        f"{EXPORTS_DIRNAME}/{stem}.html": html_mod.render(
            summary, html_mod.FILE_LINKS
        ).encode("utf-8"),
    }

    event = dataclasses.replace(
        event,
        payload={
            "summary": event.id,
            "prepared": summary.prepared.isoformat(),
            "as_of": format_ts(as_of),
            "label": summary.label,
            "slug": stem,
            # The patient's own words, kept verbatim. Nothing generated this and
            # nothing may rewrite it.
            "question": summary.question,
            "since": summary.since.isoformat() if summary.since else None,
            "since_ts": summary.since_ts,
            "since_source": reference.source,
            "demo": summary.demo,
            "exports": {
                "markdown": next(p for p in files if p.endswith(".md")),
                "html": next(p for p in files if p.endswith(".html")),
            },
            # What the sheet rested on, so a later rejection of any of it can
            # withdraw this sheet instead of it being served again.
            "cited": list(summary.cited),
            "sources": list(summary.sources),
            "height_mm": round(summary.height_mm, 1),
            "overflowed": summary.overflowed,
            "omitted": {
                section.key: section.omitted
                for section in summary.sections
                if section.omitted
            },
            "waiting": summary.waiting.to_dict(),
        },
    )
    return Prepared(event=event, summary=summary, files=files)


def write(root: Path, files: dict[str, bytes]) -> list[str]:
    """Write the export files. Binary, ``\\n`` endings already in the bytes."""
    written: list[str] = []
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # Binary, never text mode: text mode would translate newlines on
        # Windows and the same log would export different bytes per platform.
        path.write_bytes(data)
        try:
            path.chmod(EXPORT_MODE)
        except OSError:
            # A synced or restored folder loses modes routinely, and a summary
            # that refused to be written because of one would be worse than a
            # summary written at the folder's default mode.
            pass
        written.append(rel)
    return written


def prepare(
    vault,
    *,
    as_of: datetime,
    question: str = "",
    label: str = "",
    since: date | str | None = None,
    events: Sequence[Event] | None = None,
    append: Callable[[Event], Any] | None = None,
) -> Prepared:
    """Prepare a sheet: append the event, then write the two exports."""
    stream = list(events) if events is not None else list(vault.read().events)
    prepared = plan(
        stream,
        device=vault.identity.id,
        as_of=as_of,
        root=vault.root,
        question=question,
        label=label,
        since=since,
        demo=vault.is_demo,
    )
    (append or vault.append)(prepared.event)
    write(vault.root, prepared.files)
    return prepared


def export_stem(root: Path, prepared: date, label: str) -> str:
    """``2026-09-08_cardiology``, lengthened rather than overwritten on a clash.

    The same rule the raw store applies to a short-hash collision: never
    overwrite. Two sheets prepared on one day for one clinic are an ordinary
    thing — one printed before the appointment and one after — and the second
    must not silently replace the first.
    """
    slug = slugify(label) or DEFAULT_SLUG
    base = f"{prepared.isoformat()}_{slug}"
    exports = root / EXPORTS_DIRNAME
    for attempt in range(1, 100):
        stem = base if attempt == 1 else f"{base}-{attempt}"
        if not (exports / f"{stem}.md").exists() and not (
            exports / f"{stem}.html"
        ).exists():
            return stem
    raise SummaryError(
        f"there are already 99 summaries named {base} in {exports}; rename or "
        f"move some of them before preparing another."
    )


# -- reading back -----------------------------------------------------------


def find(events: Sequence[Event], summary_id: str) -> Event | None:
    for event in events:
        if event.type == EVENT_TYPE and event.id == summary_id:
            return event
    return None


def restore(events: Sequence[Event], summary_id: str) -> Restored:
    """One stored sheet, re-rendered from the log as it stood when it was made.

    Raises :class:`SummaryError` when there is no such summary. Returns a
    :class:`Restored` with ``summary=None`` when there is one and it has been
    withdrawn by a later rejection — which is a different answer from "no such
    thing" and has to stay a different answer, because the user prepared it and
    is entitled to be told what happened to it.
    """
    events = list(events)
    event = find(events, summary_id)
    if event is None:
        raise SummaryError(
            f"no summary in this record has the id {summary_id!r}. It may have "
            f"been prepared on another device that has not synced yet."
        )

    payload = event.payload
    exports = payload.get("exports")
    exports = dict(exports) if isinstance(exports, dict) else {}

    cited = [c for c in (payload.get("cited") or []) if isinstance(c, str)]
    withdrawn = tuple(sorted(set(cited) & rejected_claims(events)))
    if withdrawn:
        return Restored(event=event, summary=None, withdrawn=withdrawn, exports=exports)

    as_of = parse_ts_or_none(payload.get("as_of")) or parse_ts_or_none(event.ts)
    if as_of is None:  # pragma: no cover - an event this build wrote always has one
        raise SummaryError(
            f"summary {summary_id} records no moment it was prepared at, so it "
            f"cannot be rebuilt. The file written at the time is in exports/."
        )

    since_ts = payload.get("since_ts")
    since_day = dates_mod.parse_iso_date(payload.get("since") or "")
    reference = Reference(
        ts=since_ts if isinstance(since_ts, str) else None,
        day=since_day,
        source=str(payload.get("since_source") or FROM_WINDOW),
    )

    summary = compose(
        # Strictly before the summary event: the sheet reported the record as it
        # stood when it was prepared, and everything after it is a later fact
        # about a document already handed over.
        [e for e in events if e.sort_key < event.sort_key],
        as_of=as_of,
        question=str(payload.get("question") or ""),
        label=str(payload.get("label") or ""),
        reference=reference,
        demo=bool(payload.get("demo")),
        summary_id=event.id,
    )
    return Restored(event=event, summary=summary, exports=exports)


def listing(events: Sequence[Event]) -> list[dict[str, Any]]:
    """Every prepared sheet, newest first, with its withdrawal state.

    The question is included — it is the patient's own words and it is how they
    will recognise the sheet they want — and never the content of a withdrawn
    one.
    """
    events = list(events)
    rejected = rejected_claims(events)
    rows: list[dict[str, Any]] = []
    for event in reversed(summaries(events)):
        payload = event.payload
        cited = [c for c in (payload.get("cited") or []) if isinstance(c, str)]
        withdrawn = sorted(set(cited) & rejected)
        rows.append(
            {
                "id": event.id,
                "ts": event.ts,
                "prepared": payload.get("prepared"),
                "prepared_words": _prepared_words(event),
                "label": payload.get("label") or "",
                "question": payload.get("question") or "",
                "since": payload.get("since"),
                "since_source": payload.get("since_source"),
                "exports": payload.get("exports") or {},
                "withdrawn": len(withdrawn),
                "print_url": f"/print/{event.id}",
            }
        )
    return rows
