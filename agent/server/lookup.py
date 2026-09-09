"""Finding one artefact from what a person or a link actually typed.

Citations in the wiki use the short hash — the six characters before the
extension in a ``raw/`` filename. Sidecars and events carry the full sha256. A
URL might hold either, or a prefix of one that someone copied out of a footnote
and shortened. All three resolve here, and an ambiguous one is refused rather
than guessed at: showing the wrong photograph for a claim is worse than asking
for two more characters, because the wrong photograph looks like an answer.

Resolution goes through the log, never through the filesystem. The log is the
source of truth for what is in the record; a file sitting in ``raw/`` that no
``artifact.ingested`` event describes is not part of it and ``health-agent
check`` reports it as unrecorded. Serving it because its name happened to match
would let anything dropped into the folder be fetched through the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..ingest.store import RawStore
from ..projection.citations import Artifact
from .index import Index
from .state import Snapshot

#: Long enough that a typo is unlikely to resolve, short enough that a person
#: can type it off a printed footnote.
MIN_PREFIX = 4


class ArtifactNotFound(LookupError):
    """No artefact in this record matches. Renders as a 404."""


class ArtifactAmbiguous(LookupError):
    """More than one does. Renders as a 409, naming them."""

    def __init__(self, token: str, matches: list[str]):
        super().__init__(
            f"{token!r} matches {len(matches)} artefacts ({', '.join(matches)}); "
            f"use enough of the hash to name one"
        )
        self.matches = matches


@dataclass(frozen=True)
class Located:
    """An artefact the record describes, and the bytes on disk if they are there."""

    artifact: Artifact
    path: Path | None

    @property
    def exists(self) -> bool:
        return self.path is not None and self.path.is_file()


def resolve_short(snapshot: Snapshot, token: str, index: Index | None = None) -> str:
    """The one short hash *token* names.

    The index answers this when it can and the snapshot answers it otherwise;
    both are asked the same question and return the same answer, which is what
    makes deleting ``index.sqlite`` unobservable.
    """
    cleaned = (token or "").strip().lower()
    if not cleaned:
        raise ArtifactNotFound("no artefact hash was given")
    if cleaned in snapshot.artifacts:
        return cleaned

    matches: list[str] | None = None
    if index is not None and index.ensure(snapshot):
        matches = index.shorts_matching(cleaned)
    if matches is None:
        matches = _matches_from_snapshot(snapshot, cleaned)

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ArtifactAmbiguous(cleaned, sorted(matches))
    raise ArtifactNotFound(
        f"no artefact in this record has the hash {cleaned!r}. The hash is the six "
        f"characters before the extension in a raw/ filename, and the footnote key "
        f"in a wiki citation."
    )


def _matches_from_snapshot(snapshot: Snapshot, cleaned: str) -> list[str]:
    """The fallback the index accelerates. Same rule, no SQLite."""
    if len(cleaned) >= MIN_PREFIX:
        by_prefix = sorted(s for s in snapshot.artifacts if s.startswith(cleaned))
        if by_prefix:
            return by_prefix
    return sorted(
        short
        for short, artifact in snapshot.artifacts.items()
        if artifact.digest and artifact.digest == cleaned
    )


def locate(vault, snapshot: Snapshot, short: str) -> Located:
    """Where an artefact's bytes are, if the record's own path still resolves.

    The path is the one the ingest event recorded, validated by
    :meth:`agent.ingest.store.RawStore.resolve_recorded` — which refuses
    anything absolute or climbing out of the vault. Event payloads are ordinary
    lines in a text file that a person can hand-edit and a sync client can
    corrupt, so the path in one is checked before it is followed, exactly as the
    restore path checks it.
    """
    artifact = snapshot.artifacts[short]
    store = RawStore(vault.root, vault.profile)
    path = store.resolve_recorded(artifact.rel) if artifact.rel else None
    if path is None or not path.is_file():
        # The recorded path is gone. The bytes may still be findable by digest —
        # a resync can restore a file under the name the log already cites.
        path = store.find(artifact.digest) if artifact.digest else None
    return Located(artifact=artifact, path=path)
