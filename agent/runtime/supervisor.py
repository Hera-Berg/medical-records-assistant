"""The ``llama-server`` process: starting it, trusting it, sleeping it, stopping it.

**One process per machine.** A lock file in the data directory is held for as
long as this process may run the reader. A second process — ``health-agent
extract`` while the server is up — is refused with a sentence rather than
loading a second 3.5 GB copy on a laptop that has room for one.

**One thread owns the child.** Every launch and every stop happens on the
supervisor's own long-lived thread. On Linux the child is told to die with its
parent through ``PR_SET_PDEATHSIG``, and that signal fires when the *thread*
that forked it exits — so launching from a request-handler thread that the
server later retires would kill the reader under a running job.

**A key, even on loopback.** Any web page in any browser tab can send a request
to ``127.0.0.1``, and llama-server reflects any ``Origin`` back as allowed. So
each launch gets a fresh random key, given to the child in its environment —
never on its command line, where ``ps`` shows it to every user — and never
written anywhere. Inherited ``LLAMA_*`` variables are stripped first: llama-server
reads its flags from them, and one left in a shell profile could turn on the web
interface or a prompt log without anything here having asked for it.

**Identity before the key.** In the pinned build only ``/health`` answers
without a key, so what a port says about itself cannot establish anything. The
key is released to a client only once three things are true of *the process*:
the child this thread spawned is still running, its own output says it is
listening on the chosen port, and — on Linux — the listening socket on that port
belongs to the child's pid. Only then is an authenticated request made, and it
must report the pinned alias, the pinned build and vision among its modalities,
or the reader is stopped.

**Crashes restart with backoff, and stop restarting.** Three unexpected exits in
ten minutes leave it stopped with a sentence saying so. A child killed by
``SIGKILL`` is reported as most likely out of memory, because on a laptop that
is overwhelmingly what it is.

**It sleeps.** After ``sleep_after_minutes`` with no request in flight and no
work waiting, the process is stopped and the state says ``sleeping``. The next
job or question wakes it. Stopping the process rather than asking llama-server
to unload is deliberate: only exiting reliably returns the memory, it behaves
the same on every platform, and "never while a job is queued" stays a rule this
module enforces rather than one a server's idle timer happens to agree with.
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import json
import logging
import os
import queue as queue_mod
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx

from ..errors import ReaderUnavailable
from ..llm import credentials as credentials_mod
from ..llm import redaction
from ..llm.endpoint import parse as parse_endpoint
from ..llm.settings import DEFAULT_CTX, VlmSettings
from . import choice as choice_mod
from . import manifest, platforms, states
from .store import Store

log = logging.getLogger("agent.runtime")

#: A 4B model on a laptop CPU can take minutes over a large page, and the client
#: must not give up on a live request the way it would on a sleeping box.
READ_TIMEOUT_S = 900.0
CONNECT_TIMEOUT_S = 3.0

#: Loading 3.4 GB from a cold disk is slow; from page cache it is seconds.
START_TIMEOUT_S = 240.0
TICK_S = 0.5
RESTART_BACKOFF_S = (2.0, 8.0, 30.0)
CRASH_WINDOW_S = 600.0
CRASHES_BEFORE_STOPPING = 3

LOCK_FILENAME = "reader.lock"
PID_FILENAME = "reader.pid"
LOG_FILENAME = "reader.log"
PREVIOUS_LOG_FILENAME = "reader.previous.log"

CREDENTIAL_SOURCE = "the reader on this computer (a key made for this launch)"

_LISTENING = re.compile(r"listening on http://127\.0\.0\.1:(\d+)")


@dataclass(frozen=True)
class Launch:
    """What to run. The seam tests replace with a small fake server."""

    argv: list[str]
    cwd: Path
    binary: Path


def llama_launch(store: Store, platform: str, port: int, ctx: int) -> Launch:
    """The pinned ``llama-server``, with every flag that matters stated."""
    engine = manifest.engine_for(platform)
    binary = store.binary(engine) if engine else None
    if engine is None or binary is None:
        raise ReaderUnavailable("not-downloaded", "the reader's program is not here")
    weights = store.path_for(manifest.VISION, manifest.WEIGHTS)
    projector = store.path_for(manifest.VISION, manifest.PROJECTOR)
    return Launch(
        argv=[
            str(binary),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--model", str(weights),
            "--mmproj", str(projector),
            "--ctx-size", str(ctx),
            # The default splits the context across several slots, which would
            # quietly give each request a fraction of the room it was sized for.
            "--parallel", "1",
            # The thinking toggle is a chat-template kwarg; without Jinja it is
            # ignored and a reasoning trace lands in every answer.
            "--jinja",
            "--alias", manifest.ALIAS,
            "--no-webui",
            "--no-slots",
            "--offline",
        ],
        cwd=binary.parent,
        binary=binary,
    )


@dataclass
class Status:
    state: str
    reason: str
    port: int | None = None
    pid: int | None = None
    launches: int = 0
    log_path: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "message": states.message(self.reason),
            "launches": self.launches,
            "log_path": self.log_path,
        }


class Reader:
    """The reader on this computer. One per process; see :func:`get`."""

    def __init__(
        self,
        store: Store,
        platform: str | None = None,
        sleep_after_minutes: int = choice_mod.DEFAULT_SLEEP_MINUTES,
        ctx: int = DEFAULT_CTX,
        launcher: Callable[[Store, str, int, int], Launch] = llama_launch,
        clock: Callable[[], float] = time.monotonic,
        start_timeout_s: float = START_TIMEOUT_S,
        expected_build: str = manifest.BUILD_INFO,
        check_socket_owner: bool = True,
        restart_backoff_s: tuple[float, ...] = RESTART_BACKOFF_S,
    ):
        self.store = store
        self.platform = platform if platform is not None else platforms.current()
        self.sleep_after_minutes = sleep_after_minutes
        self.ctx = ctx
        self._launcher = launcher
        self._clock = clock
        self._start_timeout_s = start_timeout_s
        self._expected_build = expected_build
        self._check_socket_owner = check_socket_owner
        self._restart_backoff_s = restart_backoff_s
        #: Whether there is queued work. Set by the server; asked only at the
        #: moment the reader would otherwise go to sleep.
        self.has_work: Callable[[], bool] = lambda: False

        self._cond = threading.Condition()
        self._commands: queue_mod.Queue[str] = queue_mod.Queue()
        self._thread: threading.Thread | None = None
        self._closed = False

        self._state = states.NOT_DOWNLOADED
        self._reason = "not-downloaded"
        self._files_checked = False
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._key: str | None = None
        self._stopping = False
        self._listening = threading.Event()
        self._lock_handle = None
        self._launches = 0
        self._crashes: list[float] = []
        self._restart_at: float | None = None
        self._active = 0
        self._questions = 0
        self._last_used = clock()

    # -- reporting ---------------------------------------------------------

    @property
    def bundles(self) -> tuple[manifest.Bundle, ...]:
        return manifest.reader_bundles(self.platform)

    @property
    def log_path(self) -> Path:
        return self.store.logs_dir / LOG_FILENAME

    def status(self) -> Status:
        with self._cond:
            if not self.platform:
                self._state, self._reason = states.UNSUPPORTED, "unsupported-platform"
            elif self._state == states.NOT_DOWNLOADED or not self._files_checked:
                self._refresh_files()
            return Status(
                state=self._state,
                reason=self._reason,
                port=self._port,
                pid=self._proc.pid if self._proc is not None else None,
                launches=self._launches,
                log_path=str(self.log_path) if self.log_path.exists() else None,
            )

    def _refresh_files(self) -> None:
        """Called with the condition held. Cheap once files have been verified."""
        self._files_checked = True
        ready = self.store.is_ready(self.bundles) or self._preparable()
        if ready and self._state == states.NOT_DOWNLOADED:
            self._state, self._reason = states.SLEEPING, "sleeping"
        elif not ready and self._state in (states.NOT_DOWNLOADED, states.SLEEPING):
            self._state, self._reason = states.NOT_DOWNLOADED, "not-downloaded"

    def _preparable(self) -> bool:
        """Every file verified, with only an engine archive left to unpack."""
        return bool(self.bundles) and not self.store.missing(self.bundles)

    @property
    def questions_waiting(self) -> int:
        with self._cond:
            return self._questions

    # -- what a client is given ------------------------------------------

    def settings(self) -> VlmSettings:
        """Settings pointing at the running reader. Raises if it is not running."""
        with self._cond:
            if self._state != states.READY or self._port is None:
                raise ReaderUnavailable(self._reason, states.message(self._reason))
            port = self._port
        return VlmSettings(
            endpoint=parse_endpoint(f"http://127.0.0.1:{port}/v1"),
            model=manifest.ALIAS,
            ctx=self.ctx,
            connect_timeout_s=CONNECT_TIMEOUT_S,
            read_timeout_s=READ_TIMEOUT_S,
        )

    def credential_for(self, port: int | None) -> credentials_mod.Credential:
        """This launch's key — only for the port this launch was verified on.

        A client built before a restart still points at the old port. It gets a
        refusal, never the new key: the old port may by now belong to anything.
        """
        with self._cond:
            alive = self._proc is not None and self._proc.poll() is None
            if self._state != states.READY or not alive or port != self._port or not self._key:
                raise ReaderUnavailable(
                    self._reason if self._state != states.READY else "restarting",
                    "the reader on this computer is not ready on that port",
                )
            self._last_used = self._clock()
            return credentials_mod.Credential(self._key, CREDENTIAL_SOURCE)

    def runtime_facts(self) -> dict[str, Any]:
        """What an extraction records about what read it. Observations only."""
        return {
            "kind": "bundled",
            "engine": manifest.ENGINE,
            "files": {
                manifest.WEIGHTS.name: f"sha256:{manifest.WEIGHTS.sha256}",
                manifest.PROJECTOR.name: f"sha256:{manifest.PROJECTOR.sha256}",
            },
        }

    @contextlib.contextmanager
    def in_use(self) -> Iterator[None]:
        """Hold the reader awake for the length of a job or a question."""
        with self._cond:
            self._active += 1
            self._last_used = self._clock()
        try:
            yield
        finally:
            with self._cond:
                self._active -= 1
                self._last_used = self._clock()

    @contextlib.contextmanager
    def question(self) -> Iterator[None]:
        """A person is waiting for an answer. Queued documents wait behind it."""
        with self._cond:
            self._questions += 1
        try:
            with self.in_use():
                yield
        finally:
            with self._cond:
                self._questions -= 1

    # -- control -----------------------------------------------------------

    def start(self) -> None:
        """Ask for the reader to be running. Returns at once."""
        self._ensure_thread()
        self._commands.put("start")

    def retry(self) -> None:
        """Clear a stop that was waiting for a person, and start again."""
        with self._cond:
            if self._state == states.STOPPED:
                self._state, self._reason = states.SLEEPING, "sleeping"
                self._crashes.clear()
        self.start()

    def ensure_ready(self, timeout: float | None = None) -> None:
        """Start the reader if it is not running, and wait until it can be used."""
        with self._cond:
            if self._state == states.READY:
                self._last_used = self._clock()
                return
        status = self.status()
        if status.state in (states.NOT_DOWNLOADED, *states.TERMINAL):
            raise ReaderUnavailable(status.reason, states.message(status.reason))
        self.start()
        deadline = self._clock() + (timeout if timeout is not None else self._start_timeout_s + 30)
        with self._cond:
            while self._state not in (states.READY, states.NOT_DOWNLOADED, *states.TERMINAL):
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise ReaderUnavailable("starting", states.message("starting"))
                self._cond.wait(timeout=min(remaining, 1.0))
            if self._state != states.READY:
                raise ReaderUnavailable(self._reason, states.message(self._reason))
            self._last_used = self._clock()

    def stop(self, timeout: float = 20.0) -> None:
        """Put the reader to sleep now. Returns once the process has exited."""
        if self._thread is None:
            return
        self._commands.put("stop")
        deadline = self._clock() + timeout
        with self._cond:
            while self._state == states.READY or self._proc is not None:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return
                self._cond.wait(timeout=min(remaining, 0.5))

    def shutdown(self) -> None:
        """Stop the process and the thread, and let go of the machine lock."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
        if self._thread is not None:
            self._commands.put("shutdown")
            self._thread.join(timeout=20)
        else:
            self._terminate()
        self._release_lock()

    # -- the owner thread --------------------------------------------------

    def _ensure_thread(self) -> None:
        with self._cond:
            if self._closed:
                raise ReaderUnavailable("failed-to-start", "the reader has been shut down")
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="health-agent-reader", daemon=True
            )
            self._thread.start()
            atexit.register(self.shutdown)

    def _run(self) -> None:
        while True:
            try:
                command = self._commands.get(timeout=TICK_S)
            except queue_mod.Empty:
                command = None
            try:
                if command == "shutdown":
                    self._terminate()
                    return
                if command == "start":
                    self._start()
                elif command == "stop":
                    self._sleep()
                self._tick()
            except Exception:  # noqa: BLE001 - the owner thread must not die
                log.exception("reader supervisor iteration failed")
                self._set(states.STOPPED, "failed-to-start")
                self._terminate()

    def _set(self, state: str, reason: str) -> None:
        with self._cond:
            self._state, self._reason = state, reason
            self._cond.notify_all()

    def _start(self) -> None:
        with self._cond:
            if self._state in (states.READY, *states.TERMINAL):
                return
        if not self.platform:
            self._set(states.UNSUPPORTED, "unsupported-platform")
            return
        try:
            self.store.prepare(self.bundles)
        except Exception:  # noqa: BLE001 - reported as a state
            log.exception("preparing the reader's files failed")
        if not self.store.is_ready(self.bundles):
            self._set(states.NOT_DOWNLOADED, "not-downloaded")
            return
        if not self._acquire_lock():
            self._set(states.ELSEWHERE, "in-use-elsewhere")
            return
        self._reap_orphan()
        restarting = self._restart_at is not None
        self._set(states.STARTING, "restarting" if restarting else "starting")
        self._restart_at = None

        for attempt in range(2):
            port = self._port if (attempt == 0 and self._port and _port_free(self._port)) else _free_port()
            outcome = self._launch(port)
            if outcome == "port-taken":
                continue
            return
        self._set(states.STOPPED, "failed-to-start")

    def _launch(self, port: int) -> str:
        key = secrets.token_urlsafe(32)
        redaction.register(key)
        launch = self._launcher(self.store, self.platform or "", port, self.ctx)

        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("LLAMA_")}
        env["LLAMA_API_KEY"] = key

        self.store.logs_dir.mkdir(parents=True, exist_ok=True)
        if self.log_path.exists():
            os.replace(self.log_path, self.store.logs_dir / PREVIOUS_LOG_FILENAME)
        self._listening.clear()

        kwargs: dict[str, Any] = {}
        if sys.platform.startswith("linux"):
            kwargs["preexec_fn"] = _die_with_parent
        if sys.platform == "win32":  # pragma: no cover - Windows only
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            launch.argv,
            cwd=str(launch.cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
        if sys.platform == "win32":  # pragma: no cover - Windows only
            _kill_with_parent_windows(proc)
        with self._cond:
            self._proc, self._port, self._key = proc, port, None
            self._stopping = False
        self._write_pidfile(proc.pid, launch.binary)
        threading.Thread(
            target=self._pump, args=(proc, port), name="health-agent-reader-log", daemon=True
        ).start()

        deadline = self._clock() + self._start_timeout_s
        while self._clock() < deadline:
            if proc.poll() is not None:
                return self._launch_failed(proc)
            if self._listening.is_set() and _health_ok(port):
                break
            time.sleep(0.25)
        else:
            self._terminate()
            self._set(states.STOPPED, "failed-to-start")
            return "failed"

        # Identity before the key. Everything below this line is about the
        # process; nothing is sent that carries the key until it has passed.
        if proc.poll() is not None:
            return self._launch_failed(proc)
        if self._check_socket_owner and _socket_owner_known() and not _socket_owned_by(proc.pid, port):
            self._terminate()
            self._set(states.STOPPED, "not-its-port")
            return "failed"

        reason = self._check_identity(port, key)
        if reason is not None:
            self._terminate()
            self._set(states.STOPPED, reason)
            return "failed"

        with self._cond:
            self._key = key
            self._launches += 1
            self._last_used = self._clock()
            self._state, self._reason = states.READY, "ready"
            self._cond.notify_all()
        return "ready"

    def _launch_failed(self, proc: subprocess.Popen) -> str:
        """The child exited while starting. A taken port is worth one more try."""
        with self._cond:
            self._proc = None
        text = _tail(self.log_path)
        if "couldn't bind" in text.lower() or "address already in use" in text.lower():
            return "port-taken"
        self._set(states.STOPPED, _exit_reason(proc.returncode, starting=True))
        self._remove_pidfile()
        return "failed"

    def _check_identity(self, port: int, key: str) -> str | None:
        """The first request carrying the key. Must name the pinned build and alias."""
        try:
            response = httpx.get(
                f"http://127.0.0.1:{port}/props",
                headers={"Authorization": f"Bearer {key}"},
                timeout=httpx.Timeout(10.0, connect=CONNECT_TIMEOUT_S),
            )
            props = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return "failed-to-start"
        if props.get("model_alias") != manifest.ALIAS:
            return "wrong-build"
        if props.get("build_info") != self._expected_build:
            return "wrong-build"
        modalities = props.get("modalities")
        if not isinstance(modalities, dict) or modalities.get("vision") is not True:
            return "no-vision"
        return None

    def _pump(self, proc: subprocess.Popen, port: int) -> None:
        """Copy the child's output to its log, watching for the listening line."""
        stream = proc.stdout
        if stream is None:  # pragma: no cover - always piped
            return
        with open(self.log_path, "ab") as log_file:
            for raw in iter(stream.readline, b""):
                log_file.write(raw)
                log_file.flush()
                match = _LISTENING.search(raw.decode("utf-8", "replace"))
                if match and int(match.group(1)) == port:
                    self._listening.set()

    def _tick(self) -> None:
        with self._cond:
            proc = self._proc
            state = self._state
        if proc is not None and proc.poll() is not None and state in (states.READY, states.STARTING):
            with self._cond:
                expected = self._stopping
            if not expected:
                self._crashed(proc.returncode)
                return
        if state == states.STARTING and self._restart_at is not None and self._clock() >= self._restart_at:
            with self._cond:
                self._state = states.SLEEPING
            self._start()
            return
        if state == states.READY:
            self._maybe_sleep()

    def _crashed(self, returncode: int | None) -> None:
        now = self._clock()
        with self._cond:
            self._proc, self._key = None, None
            self._crashes = [t for t in self._crashes if now - t < CRASH_WINDOW_S] + [now]
            crashes = len(self._crashes)
        self._remove_pidfile()
        reason = _exit_reason(returncode, starting=False)
        log.warning("the reader exited unexpectedly (code %s)", returncode)
        if crashes >= CRASHES_BEFORE_STOPPING or reason == "out-of-memory":
            self._set(states.STOPPED, reason if reason == "out-of-memory" else "crashed-repeatedly")
            return
        backoff = self._restart_backoff_s
        self._restart_at = now + backoff[min(crashes, len(backoff)) - 1]
        self._set(states.STARTING, "restarting")

    def _maybe_sleep(self) -> None:
        minutes = self.sleep_after_minutes
        if minutes <= 0:
            return
        with self._cond:
            busy = self._active > 0 or self._questions > 0
            idle_for = self._clock() - self._last_used
        if busy or idle_for < minutes * 60:
            return
        try:
            waiting = self.has_work()
        except Exception:  # noqa: BLE001 - uncertain means stay awake
            waiting = True
        if waiting:
            with self._cond:
                self._last_used = self._clock()
            return
        self._sleep()

    def _sleep(self) -> None:
        self._terminate()
        with self._cond:
            if self._state not in states.TERMINAL and self._state != states.NOT_DOWNLOADED:
                self._state, self._reason = states.SLEEPING, "sleeping"
            self._cond.notify_all()

    def _terminate(self) -> None:
        with self._cond:
            proc = self._proc
            self._stopping = True
            self._key = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        with self._cond:
            self._proc = None
        self._remove_pidfile()

    # -- the machine lock and orphans --------------------------------------

    def _acquire_lock(self) -> bool:
        if self._lock_handle is not None:
            return True
        self.store.root.mkdir(parents=True, exist_ok=True)
        handle = open(self.store.root / LOCK_FILENAME, "a+b")
        try:
            if sys.platform == "win32":  # pragma: no cover - Windows only
                import msvcrt  # noqa: PLC0415

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._lock_handle = handle
        return True

    def _release_lock(self) -> None:
        handle, self._lock_handle = self._lock_handle, None
        if handle is not None:
            handle.close()

    def _write_pidfile(self, pid: int, binary: Path) -> None:
        body = json.dumps({"pid": pid, "binary": str(binary)}) + "\n"
        (self.store.root / PID_FILENAME).write_text(body, encoding="utf-8")

    def _remove_pidfile(self) -> None:
        with contextlib.suppress(OSError):
            (self.store.root / PID_FILENAME).unlink()

    def _reap_orphan(self) -> None:
        """Stop a reader a previous process left running, if it is really ours.

        Only called with the machine lock held, so no live owner exists. The pid
        is checked against the binary before anything is signalled: a pid is
        reused, and signalling a stranger's process because a stale file named
        its number would be a bad thing for a health record app to do.
        """
        path = self.store.root / PID_FILENAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid, binary = int(data["pid"]), str(data["binary"])
        except (OSError, ValueError, KeyError, TypeError):
            self._remove_pidfile()
            return
        if _process_is(pid, binary):
            log.warning("stopping a reader left running by an earlier process (pid %s)", pid)
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
            for _ in range(40):
                if not _process_is(pid, binary):
                    break
                time.sleep(0.25)
            else:
                with contextlib.suppress(OSError):
                    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        self._remove_pidfile()


# --- helpers ---------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _health_ok(port: int) -> bool:
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _exit_reason(returncode: int | None, starting: bool) -> str:
    killed = returncode is not None and (
        returncode == -getattr(signal, "SIGKILL", 9) or returncode == 128 + 9
    )
    if killed:
        return "out-of-memory"
    return "failed-to-start" if starting else "crashed-repeatedly"


def _tail(path: Path, limit: int = 8192) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _die_with_parent() -> None:  # pragma: no cover - runs in the child
    """``prctl(PR_SET_PDEATHSIG, SIGTERM)``: exit when the supervising thread does."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM)
    except OSError:
        pass


def _kill_with_parent_windows(proc: subprocess.Popen) -> None:  # pragma: no cover
    """Put the child in a job object that kills it when this process exits.

    Unverified: written against the Win32 documentation and never run.
    """
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
        kernel32.AssignProcessToJobObject(job, int(proc._handle))  # type: ignore[attr-defined]
        _JOBS.append(job)
    except Exception:  # noqa: BLE001
        log.warning("could not tie the reader's lifetime to this process on Windows")


_JOBS: list[Any] = []


def _socket_owner_known() -> bool:
    return sys.platform.startswith("linux") and Path("/proc/net/tcp").exists()


def _socket_owned_by(pid: int, port: int) -> bool:
    """Whether the socket listening on 127.0.0.1:*port* belongs to *pid*. Linux only."""
    wanted = f"{port:04X}"
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":  # LISTEN
                continue
            if fields[1].rsplit(":", 1)[-1] == wanted:
                inodes.add(fields[9])
    if not inodes:
        return False
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return False
    for entry in entries:
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if target.startswith("socket:[") and target[8:-1] in inodes:
            return True
    return False


def _process_is(pid: int, binary: str) -> bool:
    """Whether *pid* is alive and running *binary*. False when it cannot tell."""
    if sys.platform.startswith("linux"):
        try:
            return os.readlink(f"/proc/{pid}/exe") == str(Path(binary).resolve())
        except OSError:
            return False
    if sys.platform == "darwin":  # pragma: no cover - macOS only
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and result.stdout.strip() == binary
    return False  # pragma: no cover - Windows relies on the job object


# --- one reader per process ------------------------------------------------

_READER: Reader | None = None
_READER_LOCK = threading.Lock()


def get(vault=None) -> Reader:
    """The process's reader, created on first use with this machine's choice."""
    global _READER
    with _READER_LOCK:
        if _READER is None:
            root = vault.root if vault is not None else None
            minutes = choice_mod.DEFAULT_SLEEP_MINUTES
            if vault is not None:
                with contextlib.suppress(Exception):
                    minutes = choice_mod.load(vault).sleep_after_minutes
            _READER = Reader(Store(vault_root=root), sleep_after_minutes=minutes)
        return _READER


def install(reader: Reader | None) -> Reader | None:
    """Replace the process's reader. For tests; returns the one replaced."""
    global _READER
    with _READER_LOCK:
        previous, _READER = _READER, reader
        return previous
