"""``/api/files`` — the folder, as a folder.

The record is a folder, and the person it belongs to should be able to look at
it without leaving the app: the wiki pages as they were actually written, the
event shards as they actually are, the originals where they actually sit. This
route is that view, plus the one destructive action the app is willing to take
on its owner's behalf.

What may be deleted, and why the tiers are what they are, is in
:mod:`agent.server.files`. The short version: derived files freely, originals
deliberately, the event log never.

Nothing here is served as a document. Text comes back as a JSON string that
React renders as text — the vault's bytes are untrusted input (they arrived
through a third party's sync client) and :mod:`agent.server.serving` is the only
place in this codebase that hands them to a browser as a thing with a media
type, behind an allowlist and a policy that executes nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import files as files_mod
from ..deps import get_index, get_state
from ..index import Index
from ..state import RecordState

router = APIRouter()


def _fail(exc: files_mod.FilesError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _claim_counts(state: RecordState) -> dict[str, int]:
    """How many claims were read off each artefact.

    Already computed by reconciliation, so this costs a dictionary comprehension
    rather than a walk of the log. It is what turns "delete this file?" into
    "eleven entries in your record were read off this file — delete it?".
    """
    snapshot = state.snapshot()
    return {
        short: review.claims for short, review in snapshot.projection.artifacts.items()
    }


def _entry(entry: files_mod.Entry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "path": entry.path,
        "is_dir": entry.is_dir,
        "kind": entry.kind,
        "what": entry.what,
        "bytes": entry.bytes,
        "modified": entry.modified,
        "children": entry.children,
        "text": entry.text,
        "artifact": entry.artifact,
        "deletable": entry.deletable,
        "refusal": entry.refusal,
        "claims": entry.claims,
    }


@router.get("/api/files")
def browse(
    path: str = Query("", description="a path inside the vault; empty is the root"),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """One directory listing, or the description of one file.

    Both shapes come back from one route because the caller is a link, and a
    link does not know what is on the other end of it. Answering "that is a
    file, here is what it is" beats a 404 the client has to retry as a different
    request — and it means a path pasted into the address bar resolves to
    something either way.
    """
    try:
        rel = files_mod.normalise(path)
        claims = _claim_counts(state)
        target = files_mod.resolve(state.vault.root, rel)
        if target.is_file():
            entry = files_mod.describe(state.vault.root, target, claims=claims)
            entries: tuple[files_mod.Entry, ...] = ()
        else:
            entry = None
            entries = files_mod.listing(state.vault.root, rel, claims=claims)
    except files_mod.FilesError as exc:
        raise _fail(exc) from None

    return {
        "path": rel,
        "root": str(state.vault.root),
        "folder": files_mod.folder_note(rel) if entry is None else None,
        "crumbs": [{"label": label, "path": target} for label, target in files_mod.crumbs(rel)],
        "entry": _entry(entry) if entry is not None else None,
        "entries": [_entry(item) for item in entries],
    }


@router.get("/api/files/content")
def content(
    path: str = Query(..., description="a text file inside the vault"),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """A text file, for reading in place.

    Truncation is reported. A file quietly cut off looks like a file that ends
    there, which for an event shard would be alarming and wrong.
    """
    try:
        rel = files_mod.normalise(path)
        text, truncated, size = files_mod.read_text(state.vault.root, rel)
    except files_mod.FilesError as exc:
        raise _fail(exc) from None
    return {
        "path": rel,
        "text": text,
        "truncated": truncated,
        "bytes": size,
        "shown": len(text.encode("utf-8")),
    }


@router.delete("/api/files")
def remove(
    path: str = Query(..., description="the file or empty folder to delete"),
    state: RecordState = Depends(get_state),
    index: Index = Depends(get_index),
) -> dict[str, Any]:
    """Delete one file or one empty folder.

    The projection is left alone deliberately. It is derived from ``events/``
    and ``raw/``, neither of whose *contents* this can change — the log cannot be
    deleted at all, and an original's disappearance does not retract the claims
    read off it. What does change is whether a citation resolves, which the
    artefact route computes per request from the filesystem, so it is right on
    the next click without anything being invalidated.

    The index is discarded when anything under ``.agent/`` goes, because it lives
    there and it is a cache: rebuilt on the next request that wants it.
    """
    try:
        rel = files_mod.normalise(path)
        removed = files_mod.delete(state.vault.root, rel)
    except files_mod.FilesError as exc:
        raise _fail(exc) from None

    if rel.split("/", 1)[0] == ".agent":
        index.discard()

    return {
        "deleted": removed.path,
        "kind": removed.kind,
        "was_dir": removed.is_dir,
        # A derived file is gone until a rebuild, and saying so here is what
        # stops "my medication page disappeared" being a mystery for a week.
        "rebuildable": removed.kind == files_mod.DERIVED,
    }
