"""``/api/reader`` and ``/api/settings/reader`` — reading documents on this computer.

**The download never starts itself.** ``POST /api/reader/download`` is the only
thing that starts it, and the screen that sends it has already shown the exact
size, the hosts and where the files go. Nothing here runs at startup, and an
interrupted download stays interrupted until someone presses continue.

**The choice is this machine's.** ``POST /api/settings/reader`` writes the file
beside the device identity and never ``config.toml``: "this computer" in a
synced file is false on every other machine that reads it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ...errors import ConfigError, DownloadError
from ...runtime import choice as choice_mod
from ...runtime import manifest, platforms, supervisor
from .. import endpoint_state, reader_view
from ..deps import get_state
from ..state import RecordState

router = APIRouter()


@router.get("/api/reader")
def reader(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    return reader_view.payload(state)


@router.post("/api/reader/download")
def start_download(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    if state.vault.is_demo:
        raise HTTPException(
            status_code=409,
            detail="A demo record downloads nothing. Open your own record to set up reading.",
        )
    try:
        reader_view.downloader(state).start()
    except DownloadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    _nudge_when_done(state)
    return reader_view.payload(state)


@router.post("/api/reader/cancel")
def cancel_download(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    reader_view.downloader(state).cancel()
    return reader_view.payload(state)


@router.post("/api/reader/retry")
def retry(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    """Start a reader that stopped and is waiting for a person."""
    if not choice_mod.load(state.vault).reads_here:
        raise HTTPException(status_code=409, detail="This computer is not set to read documents.")
    supervisor.get(state.vault).retry()
    worker = getattr(state, "worker", None)
    if worker is not None:
        worker.reconfigured()
    return reader_view.payload(state)


@router.post("/api/settings/reader")
def choose(
    reads_on: str = Body(..., embed=True),
    sleep_after_minutes: int | None = Body(None, embed=True),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Which computer reads this machine's documents, and how long its reader idles."""
    if state.vault.is_demo:
        raise HTTPException(status_code=409, detail="A demo record reads nowhere.")
    if reads_on == choice_mod.THIS_COMPUTER and not manifest.reader_bundles(platforms.current()):
        raise HTTPException(
            status_code=409,
            detail=(
                "There is no build of the reader for this kind of computer, so "
                "documents cannot be read here. Choose another computer."
            ),
        )
    current = choice_mod.load(state.vault)
    minutes = current.sleep_after_minutes if sleep_after_minutes is None else sleep_after_minutes
    try:
        chosen = choice_mod.save(reads_on, minutes)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    local = supervisor.get(state.vault)
    local.sleep_after_minutes = chosen.sleep_after_minutes
    if not chosen.reads_here:
        # Nothing on this machine reads documents any more. Its memory goes back
        # now, not after the idle timer.
        local.stop()
        state.set_endpoint(endpoint_state.EndpointState())
    else:
        status = local.status()
        state.set_endpoint(endpoint_state.from_reader(status.state, status.reason, None))
    worker = getattr(state, "worker", None)
    if worker is not None:
        worker.reconfigured()
    return reader_view.payload(state)


def _nudge_when_done(state: RecordState) -> None:
    """Wake the worker when the download finishes, so a queue waiting on it drains."""
    import threading  # noqa: PLC0415

    fetcher = reader_view.downloader(state)

    def wait() -> None:
        fetcher.join()
        worker = getattr(state, "worker", None)
        if worker is not None:
            worker.reconfigured()

    threading.Thread(target=wait, name="health-agent-download-watch", daemon=True).start()
