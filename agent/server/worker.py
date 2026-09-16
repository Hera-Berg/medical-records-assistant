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

**The reader on this computer is woken for work, not for polling.** A pass
with nothing ready to read leaves a sleeping reader asleep and reports it as
sleeping. The one exception is the first pass after the server starts, which
starts the reader and runs the full probe — vision included — so its state is
known before the first document arrives, exactly as a remote box's is. Each
document is its own pass, so a question someone is waiting on goes before the
next document rather than behind the queue.

**The record lock is never held across a read.** Captures append under the
same lock, and a capture returns before any inference runs; see
:func:`agent.extract.runner.drain`.

**Speech drains first, locally, and unconditionally.** Recordings are read by
``faster-whisper`` on this machine, so nothing about typing up a voice note
needs the box — and until phase 6 the loop was structured so that it did anyway:
every drain happened inside the client context, so an unconfigured endpoint, a
sleeping box or a queue parked on a rejected key all stopped a recording being
transcribed. That was a bug rather than a design, and it defeated the reason
speech runs locally at all. A pass now transcribes what it can before it so much
as looks at the endpoint.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import Any

from ..asr import runner as speech_mod
from ..errors import EndpointNotConfigured, HealthAgentError, InferenceError
from ..extract import jobs as jobs_mod
from ..extract import probe as probe_mod
from ..extract import runner as runner_mod
from ..extract import session
from ..ingest import mime as mime_mod
from ..events.envelope import format_ts
from ..llm import redaction
from ..projection import citations as citations_mod
from ..runtime import states as reader_states
from ..runtime import supervisor
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

#: How often a pass looks again at a reader on this computer that has nothing to
#: do or cannot run yet. Cheap — a few ``stat`` calls — and short enough that a
#: finished download or a first capture is noticed promptly.
LOCAL_WAIT_SECONDS = 5.0

#: How often reachability is re-checked while the queue is empty, so that
#: ``/api/health`` does not go on reporting a state from an hour ago.
PROBE_SECONDS = 300.0


class Worker:
    """A daemon thread that drains the extraction queue when it can."""

    def __init__(
        self,
        state: RecordState,
        probe_vision: bool = True,
        speech: Any | None = None,
    ):
        self.state = state
        self.probe_vision = probe_vision
        #: Injected by tests. Left ``None``, ``faster-whisper`` is loaded on the
        #: first recording and kept for the life of the process.
        self.speech = speech
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

    def reconfigured(self) -> None:
        """The endpoint itself changed. Forget what was learned about the old one.

        Not :meth:`resume`: a new address is not a new key, and un-parking jobs
        that stopped on a rejected credential because someone corrected a
        typo in a URL would send them all at a box that will reject them again.
        What this does clear is the *stall* — "stop asking until a person acts"
        is satisfied by a person having just acted — and the probe, so the next
        pass finds out about the machine that is configured now.
        """
        self._stalled = False
        self._probed = False
        self.nudge()

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

        Speech first and outside every guard below it. A recording is read on
        this machine, so an unconfigured endpoint, a sleeping box and a queue
        parked on a rejected key are all irrelevant to it — and each of them
        used to stop it.
        """
        transcribed = self._drain_speech()

        if self._stalled:
            # Needs a person, not a retry. Keep reporting, stop asking — but the
            # speech pass above already ran, because none of what a person has
            # to fix is between a recording and its transcript.
            return IDLE_SECONDS if transcribed else PROBE_SECONDS

        try:
            local = session.reads_here(self.state.vault)
        except HealthAgentError as exc:
            self.last_error = redaction.scrub(str(exc))
            return IDLE_SECONDS if transcribed else PROBE_SECONDS
        if local:
            return self._run_local(transcribed)

        try:
            with session.open_client(self.state.vault) as client:
                if not self._probed:
                    self._run_probe(client)
                    self._probed = True
                    if self.state.endpoint.is_terminal:
                        return self._stall()
                wait = self._drain(client)
                return IDLE_SECONDS if transcribed else wait
        except EndpointNotConfigured:
            # Not a fault. A vault with no [models.vlm] table is simply not one
            # extraction runs against, and a demo vault is deliberately one.
            self.state.set_endpoint(endpoint_state.not_configured())
            return IDLE_SECONDS if transcribed else PROBE_SECONDS
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
                self._stall()
                return IDLE_SECONDS if transcribed else PROBE_SECONDS
            return IDLE_SECONDS if transcribed else UNREACHABLE_SECONDS

    def _run_local(self, transcribed: bool) -> float:
        """One pass against the reader on this computer."""
        reader = supervisor.get(self.state.vault)
        status = reader.status()
        now = format_ts(self.state.now())

        if status.state in (reader_states.NOT_DOWNLOADED, *reader_states.TERMINAL):
            # Nothing to wake. A download finishing, or a person pressing try
            # again, is picked up on a later pass without a restart.
            self.state.set_endpoint(endpoint_state.from_reader(status.state, status.reason, now))
            return IDLE_SECONDS if transcribed else LOCAL_WAIT_SECONDS

        with self.state.lock:
            queue = self.state.queue()
            runner_mod.enqueue_unread(self.state.vault, queue)
            ready = queue.ready(self.state.now())
        if self._probed and not ready and status.state != reader_states.READY:
            self.state.set_endpoint(endpoint_state.from_reader(status.state, status.reason, now))
            return IDLE_SECONDS

        try:
            with reader.in_use(), session.open_client(self.state.vault) as client:
                if not self._probed:
                    self._run_probe(client)
                    self._probed = True
                    probed = self.state.endpoint
                    if probed.state != endpoint_state.WORKING:
                        # Blind or unconstrained: the same sentences a remote
                        # box gets, said about this computer.
                        self.state.set_endpoint(
                            replace(probed, where=endpoint_state.THIS_COMPUTER)
                        )
                        return self._stall() if probed.is_terminal else LOCAL_WAIT_SECONDS
                    self.state.set_endpoint(
                        endpoint_state.from_reader(
                            reader_states.READY, "ready", now, model=client.settings.model
                        )
                    )
                else:
                    self.state.set_endpoint(
                        endpoint_state.from_reader(
                            reader_states.READY, "ready", now, model=client.settings.model
                        )
                    )
                if not ready:
                    return IDLE_SECONDS if transcribed else LOCAL_WAIT_SECONDS
                return self._drain(client, reader=reader)
        except (InferenceError, HealthAgentError) as exc:
            # A reader that stopped reports a stop; one that is starting reports
            # that. Neither stalls the loop: a person pressing "try again", or a
            # restart finishing, is picked up on the next pass by itself.
            self.last_error = redaction.scrub(str(exc))
            self.state.record_endpoint_error(exc)
            if self.state.endpoint.where != endpoint_state.THIS_COMPUTER:
                self.state.set_endpoint(
                    replace(self.state.endpoint, where=endpoint_state.THIS_COMPUTER)
                )
            return IDLE_SECONDS if transcribed else LOCAL_WAIT_SECONDS

    def _drain_speech(self) -> bool:
        """Type up every recording that is waiting. Returns whether any ran.

        Nothing in here can fail in a way the loop has to reason about: there is
        no socket, no credential and no server, so there is no unreachable state
        and no auth state. A recording that cannot be read — ffmpeg missing, a
        file that will not decode — is marked on its own job and the pass
        continues with the next one.
        """
        try:
            with self.state.lock:
                queue = self.state.queue()
                runner_mod.enqueue_unread(self.state.vault, queue)
                report = speech_mod.drain(
                    self.state.vault,
                    speech_mod.Transcriber(self.state.vault, speech=self.speech),
                    queue,
                    moment=self.state.now(),
                )
                if report.appended:
                    self.state.invalidate()
        except HealthAgentError as exc:
            # Reported, never fatal, and never allowed to stop the pass that
            # follows: a broken speech path must not take the vision path with
            # it.
            self.last_error = redaction.scrub(str(exc))
            log.warning("speech pass failed: %s", self.last_error)
            return False
        return bool(report.outcomes)

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
                        "authentication was rejected by the inference box",
                        # Not the recordings. They are read by a model on this
                        # machine, so calling them blocked on authentication
                        # would be untrue and would hide them behind a resume
                        # the user has no reason to perform.
                        artifacts=self._vision_artifacts(),
                    )
        return PROBE_SECONDS

    def _vision_artifacts(self) -> list[str]:
        """Artefacts the box reads, as opposed to the ones this machine does."""
        artifacts = citations_mod.index_artifacts(list(self.state.vault.read().events))
        return [
            short
            for short, artifact in artifacts.items()
            if not mime_mod.is_speech(artifact.mime)
        ]

    def _run_probe(self, client) -> None:
        """Learn all three states before the first real job runs.

        ``MODELS.md`` asks for this explicitly. Discovering unreachability by
        failing a ninety-second job is how a five-second answer costs an
        afternoon.
        """
        report = probe_mod.run(client, skip_vision=not self.probe_vision)
        self.state.record_probe(report)

    def _drain(self, client, reader=None) -> float:
        with self.state.lock:
            queue = self.state.queue()
            runner_mod.enqueue_unread(self.state.vault, queue)
            ready = queue.ready(self.state.now())
        if not ready:
            return IDLE_SECONDS

        extractor = runner_mod.Extractor(
            self.state.vault, client, locale=self.state.vault.config.locale
        )
        report = runner_mod.drain(
            self.state.vault,
            extractor,
            queue,
            # One document a pass on this computer, so the next pass can let a
            # waiting question go first. A remote box keeps draining.
            limit=1 if reader is not None else None,
            lock=lambda: self.state.lock,
            should_yield=(lambda: reader.questions_waiting > 0) if reader is not None else (lambda: False),
        )
        self.state.invalidate()
        if reader is not None and report.outcomes and not report.is_parked:
            return 0.0

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
