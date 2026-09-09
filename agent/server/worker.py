"""The in-process worker: one thread, the JSONL queue, and no broker.

``CLAUDE.md``: "Background work uses a simple in-process worker with a JSONL job
file so it survives restart." That is the whole specification and it rules out
the obvious alternatives — no Celery, no Redis, no second process. A job file a
person can read in five years with nothing installed is worth more here than
anything a broker offers.

What this adds to phase 4 is timing, not behaviour. The reading itself is
:func:`agent.extract.runner.drain`, unchanged: same idempotency key, same
validation, same refusal to repair malformed output. This decides *when* to call
it, and keeps :meth:`agent.server.state.RecordState.endpoint` current so
``/api/health`` can answer without opening a socket.

Three rules it exists to honour.

**Auth failure is terminal.** A ``401`` parks the queue and stops the loop
waiting for a person. ``MODELS.md``: "Do not retry with backoff. Retrying a
rotated key fifty times achieves nothing and may trip rate limiting or lockout
on the server." The thread keeps running so the state stays reportable, but it
stops asking.

**Unreachable is not.** A sleeping box is the expected condition — "the box
sleeps, the laptop drops off the tailnet, you're on a plane" — so the loop backs
off and tries again, and the queue drains by itself when the box wakes. Nothing
about this is surfaced as a failure.

**It is off by default under pytest.** A test that passes because a daemon
thread happened to be scheduled first is a test that fails on a slower machine
for reasons nobody can reproduce. A test that wants this behaviour calls
:meth:`Worker.run_once` directly and synchronously.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..errors import EndpointNotConfigured, HealthAgentError, InferenceError
from ..extract import jobs as jobs_mod
from ..extract import probe as probe_mod
from ..extract import runner as runner_mod
from ..extract import session
from ..llm import redaction
from . import endpoint_state
from .state import RecordState

log = logging.getLogger("agent.server")

#: How long the loop sleeps when there is nothing ready to run. Short enough
#: that a capture is picked up promptly, long enough to be invisible.
IDLE_SECONDS = 2.0

#: How long it waits after the box failed to answer. The per-job backoff in
#: :mod:`agent.extract.jobs` already spaces out individual retries; this stops
#: the loop itself from spinning against a closed socket.
UNREACHABLE_SECONDS = 30.0

#: How often reachability is re-checked while the queue is empty, so that
#: ``/api/health`` does not go on reporting a state from an hour ago.
PROBE_SECONDS = 300.0


class Worker:
    """A daemon thread that drains the extraction queue when it can."""

    def __init__(self, state: RecordState, probe_vision: bool = True):
        self.state = state
        self.probe_vision = probe_vision
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._probed = False
        #: Set when asking again would achieve nothing until a person acts — a
        #: rejected key, a public endpoint, a model the box does not have. The
        #: thread keeps running so the state stays reportable; it stops asking.
        self._stalled = False
        self.last_error: str | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="health-agent-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def nudge(self) -> None:
        """Tell the loop there may be work. Never blocks the caller."""
        self._wake.set()

    def resume(self) -> int:
        """Un-park after a new key was set. Returns how many jobs went back."""
        with self.state.lock:
            queue = self.state.queue()
            resumed = queue.resume()
        self._stalled = False
        self._probed = False
        self.nudge()
        return len(resumed)

    # -- the loop ----------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                wait = self.run_once()
            except Exception:  # noqa: BLE001 - a worker that dies is worse
                log.exception("extraction worker iteration failed")
                wait = UNREACHABLE_SECONDS
            self._wake.wait(timeout=wait)
            self._wake.clear()

    def run_once(self) -> float:
        """One pass. Returns how long to wait before the next one.

        Called by the loop, and called directly by tests — which is the point of
        it being a method that returns rather than a loop body.
        """
        if self._stalled:
            # Needs a person, not a retry. Keep reporting, stop asking.
            # `resume()` is what clears this.
            return PROBE_SECONDS

        try:
            with session.open_client(self.state.vault) as client:
                if not self._probed:
                    self._run_probe(client)
                    self._probed = True
                    if self.state.endpoint.is_terminal:
                        return self._stall()
                return self._drain(client)
        except EndpointNotConfigured:
            # Not a fault. A vault with no [models.vlm] table is simply not one
            # extraction runs against, and a demo vault is deliberately one.
            self.state.set_endpoint(endpoint_state.not_configured())
            return PROBE_SECONDS
        except (InferenceError, HealthAgentError) as exc:
            self.state.record_endpoint_error(exc)
            self.last_error = redaction.scrub(str(exc))
            # A 401 raised while the client is being built is the same terminal
            # condition as a 401 raised by a job, and reaching this branch is
            # the ordinary way it happens: the credential is resolved per call,
            # so a rotated key fails before any request is sent. Deciding from
            # the resulting *state* rather than from where the exception was
            # caught is what stops one of those two paths retrying for ever.
            if self.state.endpoint.is_terminal:
                return self._stall()
            return UNREACHABLE_SECONDS

    def _stall(self) -> float:
        """Stop asking, and park the queue if the reason is the key.

        The queue is only marked ``blocked-auth`` for an actual auth failure:
        a model mismatch or a public endpoint also needs a person, but calling
        those jobs "blocked on authentication" would send whoever reads the
        queue to fix the wrong thing.
        """
        self._stalled = True
        if self.state.endpoint.state == endpoint_state.UNAUTHORISED:
            with self.state.lock:
                queue = self.state.queue()
                if not queue.is_parked:
                    queue.park_for_auth(
                        "authentication was rejected by the inference box"
                    )
        return PROBE_SECONDS

    def _run_probe(self, client) -> None:
        """Learn all three states before the first real job runs.

        ``MODELS.md`` asks for this explicitly. Discovering unreachability by
        failing a ninety-second job is how a five-second answer costs an
        afternoon.
        """
        report = probe_mod.run(client, skip_vision=not self.probe_vision)
        self.state.record_probe(report)

    def _drain(self, client) -> float:
        with self.state.lock:
            queue = self.state.queue()
            runner_mod.enqueue_unread(self.state.vault, queue)
            ready = queue.ready(self.state.now())
        if not ready:
            return IDLE_SECONDS

        extractor = runner_mod.Extractor(
            self.state.vault, client, locale=self.state.vault.config.locale
        )
        with self.state.lock:
            report = runner_mod.drain(self.state.vault, extractor, queue)
            self.state.invalidate()

        if report.is_parked:
            self.state.set_endpoint(
                endpoint_state.EndpointState(
                    state=endpoint_state.UNAUTHORISED,
                    auth=endpoint_state.AUTH_FAILED,
                    reason="key-rejected",
                    checked_ts=self.state.snapshot().built_ts,
                )
            )
            return self._stall()

        unreachable = any(
            job.state == jobs_mod.UNREACHABLE for job in queue.runnable()
        )
        return UNREACHABLE_SECONDS if unreachable else IDLE_SECONDS

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "stalled": self._stalled,
            "probed": self._probed,
        }
