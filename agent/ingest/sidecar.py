"""The JSON sidecar written beside every stored artefact.

The sidecar exists for the person who opens this folder in five years with none
of this software installed. Everything in it is also in the event log, and the
log remains the source of truth — but a folder of photographs whose meaning
lives only in a JSONL file three directories away is not a record that outlives
the app. Beside each original sits a small file saying what it is, when it
arrived, and what it hashes to.

It is written once, at ingest, and never rewritten. A correction is an event,
never an edit here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .hashing import HASH_ALGO, is_valid_hash

SCHEMA_VERSION = 1

#: A sidecar is the artefact's **full name** plus ``.json``, so a photograph
#: stored as ``2026-09-08T1432Z_a3f91c.jpg`` is described by
#: ``2026-09-08T1432Z_a3f91c.jpg.json``.
#:
#: ``CLAUDE.md`` shows ``{stem}.json`` instead, replacing the extension. That
#: form breaks on an artefact which is itself JSON — a health-app export dropped
#: into the vault would have exactly the name of its own sidecar — and it leaves
#: a directory scan unable to tell a sidecar from an artefact, since both parse
#: as valid names. Appending keeps the pair adjacent and obviously related for a
#: human reading the folder, which was the point of the spec's form.
SIDECAR_SUFFIX = ".json"
SIDECAR_MODE = 0o600

_ABOUT = (
    "Sidecar for the artefact of the same name in this folder. The artefact is the "
    "original as received and is never modified. Written once at ingest; corrections "
    "live in the event log under events/, never here."
)


def build(
    *,
    artifact: str,
    digest: str,
    short: str,
    size: int,
    mime: str,
    mime_source: str,
    declared_mime: str | None,
    ingested_ts: str,
    captured_ts: str | None,
    artifact_ts: str | None,
    capture: dict[str, Any],
    event_id: str,
) -> dict[str, Any]:
    """Assemble the sidecar document.

    ``captured_ts`` and ``artifact_ts`` are written explicitly even when null.
    An absent key reads as "the question was never asked"; ``null`` says "this
    is genuinely not known", which is the distinction the whole timestamp rule
    rests on. Neither is ever filled in from ``ingested_ts``.
    """
    return {
        "about": _ABOUT,
        "schema": SCHEMA_VERSION,
        "artifact": artifact,
        "hash": digest,
        "hash_algo": HASH_ALGO,
        "short": short,
        "bytes": size,
        "mime": mime,
        "mime_source": mime_source,
        "declared_mime": declared_mime,
        "ingested_ts": ingested_ts,
        "captured_ts": captured_ts,
        "artifact_ts": artifact_ts,
        "capture": capture,
        "event": event_id,
    }


def render(data: dict[str, Any]) -> bytes:
    """Serialise a sidecar.

    Indented and sorted rather than canonical-compact: nothing hashes this file,
    and its only job is to be read by a human in a text editor.
    """
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def path_for(artifact_path: Path) -> Path:
    return artifact_path.with_name(artifact_path.name + SIDECAR_SUFFIX)


def is_sidecar_name(name: str) -> bool:
    return name.endswith(SIDECAR_SUFFIX)


def artifact_name_of(sidecar_name: str) -> str | None:
    """The artefact a sidecar filename describes, or ``None``."""
    if not is_sidecar_name(sidecar_name):
        return None
    return sidecar_name[: -len(SIDECAR_SUFFIX)] or None


def write(path: Path, data: dict[str, Any]) -> None:
    """Write a sidecar at *path*, replacing nothing that already exists."""
    body = render(data)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SIDECAR_MODE)
    with os.fdopen(fd, "wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def read(path: Path) -> dict[str, Any] | None:
    """Read a sidecar, or return ``None`` if it is missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def disagreement(data: dict[str, Any], artifact_name: str, digest: str | None = None) -> str | None:
    """Describe how a sidecar contradicts the artefact beside it.

    Only content is compared. File modes are not: a restore from backup or a
    resync through a sync client routinely drops them, and reporting that as
    damage would teach the user to ignore the report that matters.
    """
    if data.get("artifact") != artifact_name:
        return f"names artefact {data.get('artifact')!r}, not {artifact_name!r}"
    recorded = data.get("hash")
    if not is_valid_hash(recorded):
        return f"records hash {recorded!r}, which is not a sha256 digest"
    if digest is not None and recorded != digest:
        return f"records hash {recorded[:12]}… but the file hashes to {digest[:12]}…"
    return None
