"""Browsing the folder from inside the app, and what may be taken out of it.

The folder outlives the app. That is invariant 6, and it is also why this module
exists: if the answer to "where is my record" is "a folder you can open in
Finder", then the app should be able to show you that folder rather than only
the record it derives from it. Everything here is a view of real files at real
paths, named the way they are named on disk.

Two things it is careful about.

**Every path is resolved and checked against the vault root.** A path arrives
here from a query string, which is to say from anywhere. Anything absolute,
anything that climbs out with ``..``, and anything that resolves outside the
root through a symlink is refused — the same rule
:meth:`agent.ingest.store.RawStore.resolve_recorded` applies to paths recorded
in the log, for the same reason.

**Deletion is tiered by what the file is, not by where the click came from.**

* ``events/`` is **the record**. Invariant 1: the log is the only source of
  truth and everything else is derived from it. There is no delete here, at any
  tier of confirmation, because a UI that can remove a shard is a UI that can
  destroy the entire record with one mis-tap and no way back. Deleting the vault
  is the file manager's job, and the file manager is one keystroke away.
* ``raw/`` is **the evidence**. It can be deleted, because a person is entitled
  to remove a photograph of their own body from their own disk, and refusing
  would make this feature a lie. It is deleted deliberately: the caller is told
  first how many entries in the record were read off it. Nothing is removed from
  the record by this — the claims stand, their citation simply stops resolving,
  which the artefact page already renders as "the record says this exists, but
  its bytes are not in the vault".
* ``wiki/`` and ``.agent/`` are **derived**. Delete anything; a rebuild puts it
  back byte for byte. This is the case the feature is actually for.
* ``exports/``, ``bulk/`` and anything else are **yours**. Delete freely.

``config.toml`` is refused, because the server will not start without it and the
person who deletes it has to hand-write TOML to get their record back. It is a
text file in the folder and a text editor can do what this refuses to.

The scaffolded directories are refused too, and a directory with anything in it
is refused: "delete what is inside first" is a sentence; a recursive delete
behind a button is how a folder goes missing.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .. import vault as vault_mod
from ..config import CONFIG_FILENAME
from ..ingest import naming

#: How much of a text file is read for the viewer. A shard of ten thousand
#: events is not a thing to render in a browser, and the truncation is reported
#: rather than silently applied — a file that is quietly cut off looks like a
#: file that ends there, which for an event log would be alarming and wrong.
MAX_TEXT_BYTES = 256 * 1024

#: Suffixes shown as text in the app. Everything else is offered as a download
#: through the artefact route, which applies the media-type allowlist.
TEXT_SUFFIXES = frozenset(
    {".md", ".json", ".jsonl", ".toml", ".txt", ".csv", ".yaml", ".yml", ".log", ".cfg"}
)

LOG = "log"
RAW = "raw"
SIDECAR = "sidecar"
DERIVED = "derived"
YOURS = "yours"
CONFIG = "config"

#: Directories the scaffolding creates. Removable only by removing the vault.
PROTECTED_DIRS = frozenset({"", *vault_mod.VAULT_DIRS})


class FilesError(Exception):
    """A path that cannot be served or removed, with the sentence to say."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def normalise(raw: str | None) -> str:
    """The vault-relative path a request is asking about, or ``""`` for the root.

    Refuses anything absolute or containing a ``..`` segment before resolution
    even happens, so a traversal is rejected by its shape and not only by where
    it happens to land.
    """
    text = (raw or "").strip().replace("\\", "/")
    # NFC, because macOS hands back decomposed filenames and a path that came
    # from a listing must compare equal to the one that goes back out.
    text = unicodedata.normalize("NFC", text)
    # Before the strip, not after: stripping first turns "/etc/passwd" into a
    # relative path and answers a question nobody asked.
    if text.startswith("/"):
        raise FilesError("that path is absolute; every path here is inside your folder")
    text = text.strip("/")
    if not text or text == ".":
        return ""
    parts = PurePosixPath(text).parts
    if any(part in ("..", ".") for part in parts):
        raise FilesError("that path climbs out of your folder")
    return PurePosixPath(*parts).as_posix()


def resolve(root: Path, rel: str) -> Path:
    """*rel* as an absolute path, guaranteed to be inside *root*.

    Resolves symlinks before checking, so a link inside the vault pointing at
    ``/etc`` is refused rather than followed.
    """
    base = root.resolve()
    target = (base / rel).resolve() if rel else base
    if target != base and base not in target.parents:
        raise FilesError("that path is outside your folder")
    return target


def is_sidecar(name: str) -> bool:
    """Whether *name* is an artefact's sidecar.

    Phase 2's rule: a sidecar appends ``.json`` to the **full** artefact name
    rather than replacing the extension, precisely so that a directory scan can
    tell the two apart when the artefact is itself a JSON file. This is that
    scan, and it is the reason the rule is worth its awkwardness.
    """
    return name.endswith(".json") and naming.parse(name[: -len(".json")]) is not None


def classify(rel: str) -> str:
    """Which kind of thing a vault-relative path is."""
    if rel == CONFIG_FILENAME or rel == vault_mod.DEMO_MARKER_FILENAME:
        return CONFIG
    head = rel.split("/", 1)[0]
    if head == "events":
        return LOG
    if head == naming.RAW_DIRNAME:
        return SIDECAR if is_sidecar(rel.rsplit("/", 1)[-1]) else RAW
    if head in ("wiki", ".agent"):
        return DERIVED
    return YOURS


#: What each kind of thing is, in the words of the person whose record it is.
#: A folder gets its own sentence where "file" would have read as a mistake.
DIR_WORDS: dict[str, str] = {
    LOG: (
        "Your record itself, one file per device per month. Nothing here is ever "
        "changed or deleted — a correction is a new line — so the app keeps all "
        "of it and everything else is worked out from it."
    ),
    RAW: "Your original documents, filed by the month they were added.",
    DERIVED: "Worked out from your record. A rebuild writes all of it again.",
    YOURS: "Your own folder. Nothing in the record depends on it.",
    CONFIG: "How this app is set up for you.",
}
DIR_WORDS[SIDECAR] = DIR_WORDS[RAW]

KIND_WORDS: dict[str, str] = {
    LOG: (
        "Your record itself. Every page in the wiki is worked out from these "
        "lines, and nothing here is ever changed or removed — a correction is a "
        "new line."
    ),
    RAW: (
        "An original document, exactly as it arrived. Never altered, and what "
        "every citation in your record points at."
    ),
    DERIVED: (
        "Worked out from your record. Deleting it loses nothing: rebuilding "
        "writes it again, byte for byte."
    ),
    SIDECAR: (
        "What the record knows about the document beside it — its fingerprint, "
        "its type, when it arrived. Written in plain text so the folder still "
        "explains itself with no app installed."
    ),
    YOURS: "Your own file. The app put it here or you did; nothing depends on it.",
    CONFIG: "How this app is set up for you.",
}


@dataclass(frozen=True)
class Entry:
    """One row of a directory listing."""

    name: str
    path: str
    is_dir: bool
    kind: str
    what: str
    bytes: int | None
    modified: str | None
    children: int | None
    text: bool
    artifact: str | None
    deletable: bool
    refusal: str | None
    claims: int | None


def refusal_for(root: Path, rel: str, target: Path, kind: str) -> str | None:
    """Why *rel* may not be deleted, or ``None`` if it may.

    Every branch returns a sentence a person can act on. "Forbidden" is not an
    explanation, and this is the one screen where the app says no to its owner
    about their own files.
    """
    if not rel:
        return "This is the folder itself."
    if kind == LOG:
        return (
            "This is your record itself — everything else is worked out from it. "
            "The app will not delete it. Your file manager will, if that is really "
            "what you want."
        )
    if rel == CONFIG_FILENAME:
        return (
            "The app will not start without this. Open it in a text editor to "
            "change it."
        )
    if target.is_dir():
        if rel in PROTECTED_DIRS:
            return "This folder is part of the layout the app expects."
        try:
            if any(target.iterdir()):
                return "This folder is not empty. Delete what is inside it first."
        except OSError as exc:
            return f"This folder could not be read: {exc}"
    return None


def listing(
    root: Path,
    rel: str,
    claims: dict[str, int] | None = None,
) -> tuple[Entry, ...]:
    """One directory, sorted folders-first and then by name.

    Code-point sorting, not locale: the same folder must list in the same order
    on every machine that opens it, and this project fixes its collation
    everywhere else for the same reason.
    """
    target = resolve(root, rel)
    if not target.is_dir():
        raise FilesError(f"{rel or '.'} is not a folder in your record", status=404)

    entries: list[Entry] = []
    for child in target.iterdir():
        try:
            entries.append(describe(root, child, claims=claims))
        except OSError:
            # A file that vanished between the scan and the stat — a sync client
            # is writing in this folder. Skipping it beats failing the listing.
            continue
    entries.sort(key=lambda entry: (not entry.is_dir, entry.name))
    return tuple(entries)


def describe(root: Path, target: Path, claims: dict[str, int] | None = None) -> Entry:
    """One entry, with everything the screen needs to render and gate it."""
    base = root.resolve()
    rel = target.resolve().relative_to(base).as_posix()
    kind = classify(rel)
    stat = target.stat()
    is_dir = target.is_dir()

    parsed = naming.parse(target.name) if kind == RAW and not is_dir else None
    short = parsed.short if parsed else None

    return Entry(
        name=target.name,
        path=rel,
        is_dir=is_dir,
        kind=kind,
        what=(DIR_WORDS if is_dir else KIND_WORDS).get(kind, KIND_WORDS[YOURS]),
        bytes=None if is_dir else stat.st_size,
        modified=_stamp(stat.st_mtime),
        children=_count(target) if is_dir else None,
        text=(not is_dir) and target.suffix.lower() in TEXT_SUFFIXES,
        artifact=short,
        deletable=refusal_for(root, rel, target, kind) is None,
        refusal=refusal_for(root, rel, target, kind),
        claims=(claims or {}).get(short) if short else None,
    )


def read_text(root: Path, rel: str) -> tuple[str, bool, int]:
    """``(text, truncated, size)`` for a file the viewer can show.

    Decoded as UTF-8 with replacement rather than strictly: a shard with one
    corrupted byte is exactly the file someone opens this screen to look at, and
    refusing to show it would hide the thing they came to see.
    """
    target = resolve(root, rel)
    if not target.is_file():
        raise FilesError(f"{rel} is not a file in your record", status=404)
    if target.suffix.lower() not in TEXT_SUFFIXES:
        raise FilesError(
            f"{target.name} is not a text file. Open it from the timeline, where "
            f"the record serves documents with the right type.",
            status=415,
        )
    size = target.stat().st_size
    with target.open("rb") as handle:
        head = handle.read(MAX_TEXT_BYTES)
    return head.decode("utf-8", errors="replace"), size > MAX_TEXT_BYTES, size


def delete(root: Path, rel: str) -> Entry:
    """Remove one file or one empty directory, or refuse with a sentence.

    Returns what was removed, described as it was immediately before removal, so
    the caller can say what happened rather than echo back the path it sent.
    """
    target = resolve(root, rel)
    if not target.exists():
        raise FilesError(f"there is no {rel} in your record", status=404)
    entry = describe(root, target)
    if entry.refusal:
        raise FilesError(entry.refusal, status=403)
    try:
        if target.is_dir():
            target.rmdir()
        else:
            target.unlink()
    except OSError as exc:
        raise FilesError(f"could not delete {rel}: {exc}", status=500) from None
    return entry


def folder_note(rel: str) -> str:
    """What the folder being looked at is, said once above its listing.

    Once, rather than on every row: ``events/`` holds one file per device per
    month and repeating "this is your record itself" against each of them is
    how a screen teaches a reader to stop reading it.
    """
    return DIR_WORDS.get(classify(rel), DIR_WORDS[YOURS]) if rel else (
        "Everything the record is, as it sits on your disk. The app reads and "
        "writes these files and nothing else."
    )


def crumbs(rel: str) -> tuple[tuple[str, str], ...]:
    """``(label, path)`` from the root down to *rel*, for the trail on screen."""
    trail: list[tuple[str, str]] = [("Your folder", "")]
    walked: list[str] = []
    for part in PurePosixPath(rel).parts if rel else ():
        walked.append(part)
        trail.append((part, "/".join(walked)))
    return tuple(trail)


def _count(target: Path) -> int | None:
    try:
        return sum(1 for _ in target.iterdir())
    except OSError:
        return None


def _stamp(mtime: float) -> str:
    return (
        datetime.fromtimestamp(mtime, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
