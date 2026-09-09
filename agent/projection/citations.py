"""Footnotes that still work when the app is gone.

Every sentence in the wiki carries a footnote, and every footnote resolves to a
relative path inside the vault — an original file under ``raw/``, or the event
log line that recorded what the user said. Someone opening the folder in a text
editor in five years follows the path by hand and finds the evidence. Nothing
here needs an index, a database, or a running server to be readable.

Footnote text names **which** timestamp it is quoting. The four never mean the
same thing, and a footnote that says "2 September 2026" without saying whether
that is when the photo was taken or when it landed in the vault is how a
three-day-old photograph quietly becomes a three-day-old event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from ..events import log as log_mod
from ..events.envelope import Event, parse_ts_or_none
from . import dates


@dataclass(frozen=True)
class Artifact:
    """What the log records about one stored file."""

    short: str
    digest: str
    rel: str
    mime: str
    ingested_ts: str | None = None
    captured_ts: str | None = None
    artifact_ts: str | None = None
    source: str | None = None
    reseen_count: int = 0


@dataclass(frozen=True)
class Citation:
    """One footnote: its label, its description, and where it points."""

    key: str
    text: str
    target: str | None = None
    resolved: bool = True

    @property
    def marker(self) -> str:
        return f"[^{self.key}]"

    def definition(self) -> str:
        if self.target:
            return f"[^{self.key}]: {self.text} → `{self.target}`"
        return f"[^{self.key}]: {self.text}"


def _describe_kind(mime: str, source: str | None) -> str:
    """What kind of thing this is, from the mime type alone.

    Deliberately not "photographed prescription": what a document *is* is the
    model's reading of it, and this line is written before anything has read it.
    """
    if mime.startswith("image/"):
        return "Photograph" if source in ("camera", "recorder") else "Image"
    if mime == "application/pdf":
        return "PDF document"
    if mime.startswith("audio/"):
        return "Audio recording"
    if mime.startswith("video/"):
        return "Video recording"
    if mime.startswith("text/"):
        return "Text file"
    return "File"


def artifact_noun(mime: str, source: str | None = None) -> str:
    """"A photograph", "An audio recording" — the same words the footnote uses.

    Shared with the timeline so the two never drift: a page that footnotes a file
    as an audio recording while the timeline calls it a file is describing one
    artefact in two vocabularies, and a reader has to work out they are the same
    thing.
    """
    kind = _describe_kind(mime, source)
    article = "An" if kind[0] in "AEIOU" else "A"
    return f"{article} {kind[0].lower()}{kind[1:]}" if kind != "PDF document" else "A PDF document"


def _capture_verb(mime: str, source: str | None) -> str:
    """How this artefact came to exist at ``captured_ts``.

    A pathology PDF downloaded from a portal was not photographed, and neither
    was a voice note. The verb is small and it is the kind of wrongness that
    makes a record read as machine-generated.
    """
    if mime.startswith(("audio/", "video/")):
        return "recorded"
    if mime.startswith("image/") and source in ("camera", "recorder"):
        return "photographed"
    return "captured"


def _date_phrase(stamp: str | None) -> str | None:
    parsed = parse_ts_or_none(stamp) if stamp else None
    return dates.render_date(parsed.date()) if parsed else None


def index_artifacts(events: Iterable[Event]) -> dict[str, Artifact]:
    """Index every ingested artefact by the short hash claims cite it with."""
    artifacts: dict[str, Artifact] = {}
    reseen: dict[str, int] = {}
    for event in events:
        payload = event.payload
        short = payload.get("short")
        if not isinstance(short, str) or not short:
            continue
        if event.type == "artifact.reseen":
            reseen[short] = reseen.get(short, 0) + 1
            continue
        if event.type != "artifact.ingested":
            continue
        rel = payload.get("path")
        capture = payload.get("capture")
        artifacts[short] = Artifact(
            short=short,
            digest=str(payload.get("hash") or ""),
            rel=str(rel) if isinstance(rel, str) else "",
            mime=str(payload.get("mime") or ""),
            ingested_ts=_string_or_none(payload.get("ingested_ts")) or event.ts,
            captured_ts=_string_or_none(payload.get("captured_ts")),
            artifact_ts=_string_or_none(payload.get("artifact_ts")),
            source=(capture or {}).get("source") if isinstance(capture, dict) else None,
        )
    return {
        short: (
            artifact
            if short not in reseen
            else Artifact(**{**artifact.__dict__, "reseen_count": reseen[short]})
        )
        for short, artifact in artifacts.items()
    }


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def for_artifact(artifact: Artifact) -> Citation:
    """The footnote for a stored file."""
    kind = _describe_kind(artifact.mime, artifact.source)
    verb = _capture_verb(artifact.mime, artifact.source)

    parts: list[str] = [kind]
    captured = _date_phrase(artifact.captured_ts)
    if captured:
        parts.append(f"{verb} {captured}")
    ingested = _date_phrase(artifact.ingested_ts)
    if ingested:
        parts.append(f"added to the record {ingested}")
    dated = _date_phrase(artifact.artifact_ts)
    if dated:
        parts.append(f"document dated {dated}")

    return Citation(key=artifact.short, text=", ".join(parts), target=artifact.rel or None)


def for_event(event_id: str, ts: str, device: str, description: str) -> Citation:
    """The footnote for something the user said, which cites the log itself."""
    parsed = parse_ts_or_none(ts)
    when = dates.render_date(parsed.date()) if parsed else ts
    target = None
    if parsed is not None and device:
        target = f"events/{log_mod.shard_name(parsed.strftime('%Y-%m'), device)}"
    return Citation(key=f"ev-{event_id}", text=f"{description}, {when}", target=target)


def missing(key: str) -> Citation:
    """A citation whose artefact the log does not describe.

    Rendered rather than dropped. A footnote that says the evidence is missing
    is a report; a sentence that quietly loses its footnote is a lie about how
    well sourced the record is.
    """
    return Citation(
        key=key,
        text=(
            f"artefact {key} is cited by a claim but no ingest event describes it; "
            f"run `health-agent check`"
        ),
        target=None,
        resolved=False,
    )


class Citer:
    """Resolves claim citations against the artefacts and events in one log."""

    def __init__(self, artifacts: Mapping[str, Artifact], events: Iterable[Event]):
        self.artifacts = dict(artifacts)
        self._events = {event.id: event for event in events}
        self.unresolved: set[str] = set()

    def cite(self, key: str, description: str = "Your correction") -> Citation:
        if key.startswith("ev-"):
            event = self._events.get(key[3:])
            if event is None:
                self.unresolved.add(key)
                return missing(key)
            return for_event(event.id, event.ts, event.device, description)
        artifact = self.artifacts.get(key)
        if artifact is None:
            self.unresolved.add(key)
            return missing(key)
        return for_artifact(artifact)
