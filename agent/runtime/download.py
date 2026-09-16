"""The one-time download of the reader's files.

This is the only outbound connection the app makes to anything other than a
machine the user owns, so it runs under rules of its own.

**Never automatic.** Nothing in this module is called at startup or by the
worker. It runs from ``POST /api/reader/download`` or ``health-agent reader
download``, both of which follow a screen or a line that has already said the
exact size, the hosts and where the files go. An interrupted download stays
interrupted until someone asks for it to continue.

**Only allowlisted hosts, only https, checked on every redirect hop.** Redirects
are followed by hand rather than by ``httpx``, because the hop that matters is
the one after the first: Hugging Face answers a file request by sending the
client to a CDN host, and a redirect-following client would go wherever it was
told.

**Nothing is sent but the request for the file.** No cookie, no token, no
header naming this app or anything in the record.

**Resumable, and never trusting a resume blindly.** A ``.part`` file grows
across attempts through ``Range`` requests. A server that answers a range
request with the whole file gets the part truncated and restarted rather than
appended to — appending would build a file that is the right size and the wrong
bytes, which the hash would catch, but only after the whole download again.

**Verified before use.** sha256 over the finished part, then an atomic rename.
A mismatch deletes the part and stops, with no retry loop: a file that arrived
wrong once is a reason for a person to look, not for a machine to fetch it
again all night.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass, replace
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from ..errors import DownloadError
from . import manifest, platforms
from .manifest import Bundle, RemoteFile
from .store import CHUNK, Store

log = logging.getLogger("agent.runtime")

MAX_REDIRECTS = 8
#: Room left over on the disk after everything has arrived. A download that
#: fills a disk to the last byte breaks the vault's own writes, which matter
#: more than the model does.
SPACE_MARGIN_BYTES = 512 * 1024**2

IDLE = "idle"
DOWNLOADING = "downloading"
VERIFYING = "verifying"
UNPACKING = "unpacking"
STOPPED = "stopped"
FAILED = "failed"
COMPLETE = "complete"

#: A connect timeout that notices no network quickly, and a read timeout long
#: enough for a slow link that is still moving.
TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)


@dataclass(frozen=True)
class Progress:
    """Where a download has got to. Numbers and codes only — no far-end text."""

    state: str = IDLE
    total_bytes: int = 0
    done_bytes: int = 0
    current: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "total_bytes": self.total_bytes,
            "done_bytes": self.done_bytes,
            "current": self.current,
            "reason": self.reason,
        }


class Downloader:
    """Fetches, verifies and unpacks a set of bundles, once, on request."""

    def __init__(
        self,
        store: Store,
        bundles: Callable[[], tuple[Bundle, ...]],
        transport: httpx.BaseTransport | None = None,
    ):
        self.store = store
        self._bundles = bundles
        self._transport = transport
        self._lock = threading.Lock()
        self._progress = Progress()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    # -- reporting ---------------------------------------------------------

    def bundles(self) -> tuple[Bundle, ...]:
        """What a run would fetch, for the machine's choice as it stands now."""
        return self._bundles()

    def progress(self) -> Progress:
        with self._lock:
            current = self._progress
        if current.state in (DOWNLOADING, VERIFYING, UNPACKING):
            return current
        bundles = self._bundles()
        total = sum(bundle.size for bundle in bundles)
        done = sum(
            self.store.arrived_bytes(bundle, item)
            for bundle in bundles
            for item in bundle.files
        )
        state = current.state
        if self.store.is_ready(bundles):
            state = COMPLETE
        elif state == COMPLETE:
            state = IDLE
        return replace(current, state=state, total_bytes=total, done_bytes=done)

    def _set(self, **changes) -> None:
        with self._lock:
            self._progress = replace(self._progress, **changes)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- control -----------------------------------------------------------

    def start(self) -> bool:
        """Begin or continue in the background. Returns whether it started."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._cancel.clear()
            self._progress = replace(self._progress, state=DOWNLOADING, reason=None)
            self._thread = threading.Thread(
                target=self._run_safely, name="health-agent-download", daemon=True
            )
            self._thread.start()
            return True

    def cancel(self) -> None:
        """Stop between chunks. What has arrived is kept for the next attempt."""
        self._cancel.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run_safely(self) -> None:
        try:
            self.run()
        except DownloadError as exc:
            log.warning("download stopped: %s", exc)
            self._set(state=STOPPED if exc.reason == "cancelled" else FAILED,
                      reason=exc.reason, current=None)
        except Exception:  # noqa: BLE001 - a thread that dies silently is worse
            log.exception("download failed unexpectedly")
            self._set(state=FAILED, reason="unexpected", current=None)

    # -- the work ----------------------------------------------------------

    def run(self) -> None:
        """Fetch whatever is missing, verify it, unpack engines. Synchronous."""
        bundles = self._bundles()
        missing = self.store.missing(bundles)
        total = sum(bundle.size for bundle in bundles)
        remaining = sum(
            item.size - self.store.arrived_bytes(bundle, item)
            for bundle, item in missing
        )
        self._check_space(remaining)
        self._set(state=DOWNLOADING, total_bytes=total, reason=None)
        with httpx.Client(
            timeout=TIMEOUT,
            transport=self._transport,
            follow_redirects=False,
            verify=True,
            headers={"User-Agent": "python-httpx"},
        ) as client:
            for bundle, item in missing:
                self._fetch(client, bundle, item)

        self._set(state=UNPACKING, current=None)
        self.store.prepare(bundles)
        self._set(state=COMPLETE, current=None, done_bytes=total)

    def _check_space(self, remaining: int) -> None:
        if remaining <= 0:
            return
        free = platforms.free_disk_bytes(self.store.root)
        if free is not None and free < remaining + SPACE_MARGIN_BYTES:
            raise DownloadError(
                "no-space",
                f"{remaining} bytes are still to arrive and {free} are free where "
                f"the reader's files go ({self.store.root}); nothing was fetched",
            )

    def _fetch(self, client: httpx.Client, bundle: Bundle, item: RemoteFile) -> None:
        part = self.store.part_for(bundle, item)
        part.parent.mkdir(parents=True, exist_ok=True)
        self._set(current=item.name)

        have = part.stat().st_size if part.exists() else 0
        if have > item.size:
            part.unlink()
            have = 0

        if have < item.size:
            self._stream(client, bundle, item, part, have)

        self._set(state=VERIFYING)
        digest = hashlib.sha256()
        with part.open("rb") as handle:
            for block in iter(lambda: handle.read(CHUNK), b""):
                if self._cancel.is_set():
                    raise DownloadError("cancelled", "the download was stopped")
                digest.update(block)
        if digest.hexdigest() != item.sha256:
            part.unlink(missing_ok=True)
            raise DownloadError(
                "hash-mismatch",
                f"{item.name} arrived with the wrong contents: its sha256 does not "
                f"match the pinned value, so it was deleted and not used",
            )
        os.replace(part, self.store.path_for(bundle, item))
        self.store.remember(bundle, item)
        self._set(state=DOWNLOADING)

    def _stream(
        self, client: httpx.Client, bundle: Bundle, item: RemoteFile, part, have: int
    ) -> None:
        url = item.url
        headers = {"Range": f"bytes={have}-"} if have else {}
        for _ in range(MAX_REDIRECTS + 1):
            _require_allowed(url)
            try:
                with client.stream("GET", url, headers=headers) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise DownloadError(
                                "http-status", f"a redirect for {item.name} had no target"
                            )
                        url = urljoin(url, location)
                        continue
                    if response.status_code == 416 and have == item.size:
                        return
                    if response.status_code == 206 and have:
                        mode = "ab"
                    elif response.status_code == 200:
                        # The server sent the whole file. Start again from zero
                        # rather than appending a second copy onto the first.
                        have = 0
                        mode = "wb"
                    else:
                        raise DownloadError(
                            "http-status",
                            f"fetching {item.name} was answered with HTTP "
                            f"{response.status_code}",
                        )
                    self._write(response, bundle, item, part, mode, have)
                    return
            except httpx.HTTPError:
                raise DownloadError(
                    "network",
                    f"the connection dropped while fetching {item.name}; what arrived "
                    f"is kept, and continuing picks up from there",
                ) from None
        raise DownloadError("http-status", f"too many redirects fetching {item.name}")

    def _write(self, response, bundle: Bundle, item: RemoteFile, part, mode: str, have: int) -> None:
        written = have
        base_done = self._done_excluding(item, bundle)
        with part.open(mode) as handle:
            for block in response.iter_bytes(CHUNK):
                if self._cancel.is_set():
                    handle.flush()
                    os.fsync(handle.fileno())
                    raise DownloadError("cancelled", "the download was stopped")
                handle.write(block)
                written += len(block)
                if written > item.size:
                    break
                self._set(done_bytes=base_done + written)
            handle.flush()
            os.fsync(handle.fileno())
        if written > item.size:
            part.unlink(missing_ok=True)
            raise DownloadError(
                "hash-mismatch",
                f"{item.name} arrived larger than its pinned size, so it was deleted",
            )
        if written < item.size:
            raise DownloadError(
                "network",
                f"{item.name} stopped arriving part-way; what arrived is kept, and "
                f"continuing picks up from there",
            )

    def _done_excluding(self, item: RemoteFile, owner: Bundle) -> int:
        return sum(
            self.store.arrived_bytes(bundle, other)
            for bundle in self._bundles()
            for other in bundle.files
            if not (bundle.id == owner.id and other.name == item.name)
        )


def _require_allowed(url: str) -> None:
    split = urlsplit(url)
    if split.scheme != "https" or not manifest.host_allowed(split.hostname):
        raise DownloadError(
            "host-not-allowed",
            f"the download was sent to {split.scheme}://{split.hostname}, which is not "
            f"one of the hosts this app fetches its model files from, so nothing was "
            f"requested from it",
        )
