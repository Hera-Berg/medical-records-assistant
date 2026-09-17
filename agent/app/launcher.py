"""Start the server, open the browser, sit in the tray, and stop everything on Quit.

**One process.** The tray runs on the main thread, because macOS requires it,
and the server runs on a thread beside it. A separate server process would add
a second place for an orphan to come from — the tray dies, the server survives,
and the reader with it. In one process, quitting the app is the server's
shutdown, and the server's shutdown is the reader's.

**127.0.0.1, and no parameter to change it.** The launcher has no host setting
at all; the address guard in :mod:`agent.server.runtime` still runs.

**Three modes.** *setup* before a record folder is chosen or when it cannot be
opened, *record* once it is, and *stopped*. Each mode is a different ASGI app on
the same port; changing mode stops one server and starts the next, and the page
in the browser follows by asking ``/api/setup`` and ``/api/health``.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable

from .. import device as device_mod
from .. import distribution
from .. import vault as vault_mod
from ..errors import HealthAgentError, VaultError
from ..runtime import choice as choice_mod
from ..runtime import supervisor
from ..server import endpoint_state
from ..server.runtime import DEFAULT_HOST, check_host
from . import logs as logs_mod
from . import setup as setup_mod
from . import setup_app as setup_app_mod
from . import status as status_mod
from .instance import Instance

log = logging.getLogger("agent.app")

DEFAULT_PORT = 7777

SETUP = "setup"
RECORD = "record"
STOPPED = "stopped"
PORT_BUSY = "port-busy"
STARTING = "starting"

#: How long a server gets to bind and finish its startup.
START_TIMEOUT_S = 30.0


class Served:
    """One uvicorn server on a thread."""

    def __init__(self, app, port: int) -> None:
        import uvicorn  # noqa: PLC0415 - slow to import, and only needed here

        self.port = port
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=check_host(DEFAULT_HOST),
                port=port,
                log_config=None,
                access_log=False,
                lifespan="on",
            )
        )
        self.thread = threading.Thread(target=self._run, name="health-agent-server", daemon=True)
        self.failed: BaseException | None = None

    def _run(self) -> None:
        try:
            self.server.run()
        except BaseException as exc:  # noqa: BLE001 - uvicorn exits a failed bind with SystemExit
            self.failed = exc

    def start(self) -> bool:
        self.thread.start()
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.server.started:
                return True
            if not self.thread.is_alive():
                return False
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=60)


def port_free(port: int) -> bool:
    """Whether a server could listen on *port* now.

    The probe sets the same option the server does. Without ``SO_REUSEADDR`` a
    port whose last connections are still in TIME_WAIT — every port this app
    has just stopped serving on — reads as taken for a minute or more. On
    Windows that option means something else entirely (two programs may bind one
    port), so there the probe asks for the port exclusively instead, which fails
    whenever anything at all holds it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if sys.platform == "win32":  # pragma: no cover - Windows only
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # type: ignore[attr-defined]
        else:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((DEFAULT_HOST, port))
        except OSError:
            return False
    return True


class Controller:
    """Everything the tray can ask for, and the one thread that changes mode."""

    def __init__(
        self,
        open_browser: bool = True,
        browser: Callable[[str], object] = webbrowser.open,
        port: int | None = None,
        instance: Instance | None = None,
    ) -> None:
        self.open_on_start = open_browser
        self._browser = browser
        self._port_override = port
        self.instance = instance or Instance()
        self.mode = STARTING
        self.port = port or DEFAULT_PORT
        self.vault: vault_mod.Vault | None = None
        self.app = None
        self.problem: setup_app_mod.Problem | None = None
        self._served: Served | None = None
        self._lock = threading.RLock()
        self._requests: list[str] = []
        self._wake = threading.Condition(self._lock)
        self.stopped = threading.Event()
        self.on_change: Callable[[], None] = lambda: None
        self._thread: threading.Thread | None = None

    # -- what the tray shows -------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://{DEFAULT_HOST}:{self.port}/"

    def open_label(self) -> str:
        if self.mode == PORT_BUSY:
            return f"Open — another program is using port {self.port}"
        if self.mode == STARTING:
            return "Open — starting…"
        return "Open"

    def can_open(self) -> bool:
        return self.mode in (SETUP, RECORD)

    def status_line(self) -> str:
        if self.mode == STARTING:
            return "Starting…"
        if self.mode == PORT_BUSY:
            return f"Not running: port {self.port} is taken"
        if self.mode == SETUP:
            return (
                "Finish setting up in your browser"
                if self.problem is None
                else "Needs attention — open to see why"
            )
        if self.mode == RECORD and self.vault is not None:
            return status_mod.reader_line(self._reader_state())
        return "Stopped"

    def folder(self) -> Path | None:
        return self.vault.root if self.vault is not None and self.mode == RECORD else None

    def folder_label(self) -> str:
        return "Open the folder" if self.folder() is not None else "Open the folder — not chosen yet"

    def _reader_state(self) -> endpoint_state.EndpointState:
        record = getattr(getattr(self.app, "state", None), "record", None)
        try:
            if choice_mod.load(self.vault).reads_here:
                current = supervisor.get(self.vault).status()
                return endpoint_state.from_reader(current.state, current.reason, None)
        except HealthAgentError:
            pass
        return record.endpoint if record is not None else endpoint_state.EndpointState()

    # -- what the tray can do ------------------------------------------------

    def open_browser(self, *_: object) -> None:
        if self.can_open():
            self._browser(self.url)

    def open_folder(self, *_: object) -> None:
        folder = self.folder()
        if folder is None:
            return
        if sys.platform == "win32":  # pragma: no cover - Windows only
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":  # pragma: no cover - macOS only
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def quit(self, *_: object) -> None:
        if self._thread is None:
            # Never started — a signal or a tray failure before the mode thread
            # existed. Nothing would read the request, so stop here and now.
            self._shutdown()
            return
        self._ask("quit")

    # -- the mode thread -----------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="health-agent-app", daemon=True)
        self._thread.start()

    def wait(self, timeout: float | None = None) -> bool:
        return self.stopped.wait(timeout)

    def _ask(self, request: str) -> None:
        with self._wake:
            self._requests.append(request)
            self._wake.notify_all()

    def _loop(self) -> None:
        try:
            self._boot(first=True)
            while True:
                with self._wake:
                    while not self._requests:
                        self._wake.wait()
                    request = self._requests.pop(0)
                if request == "quit":
                    break
                if request == "reopen":
                    self._boot(first=False)
        except Exception:  # noqa: BLE001 - the app must still quit cleanly
            log.exception("the app's mode thread failed")
        finally:
            self._shutdown()

    def _boot(self, first: bool) -> None:
        previous_port = None if first else self.port
        self._stop_server()
        mode, app, port = self._decide(previous_port)
        self.port = port
        if not port_free(port):
            self._set_mode(PORT_BUSY)
            log.warning("port %s is in use by another program", port)
            self.instance.publish(None)
            return
        served = Served(app, port)
        if not served.start():
            self._set_mode(PORT_BUSY if not port_free(port) else STOPPED)
            log.error("the server did not start: %r", served.failed)
            return
        self._served = served
        self.instance.publish(self.url)
        self._set_mode(mode)
        if first and self.open_on_start:
            self._browser(self.url)

    def _decide(self, previous_port: int | None = None):
        """Which app to serve now, and on which port.

        Once a page is open, the port stays put for the rest of the session: the
        browser tab is at that address, and a record whose settings name
        another port moves there the next time the app opens.
        """
        self.vault, self.app, self.problem = None, None, None
        default_port = self._port_override or previous_port or DEFAULT_PORT

        def setup(problem: setup_app_mod.Problem | None):
            self.problem = problem
            app = setup_app_mod.create_setup_app(
                problem,
                on_chosen=lambda: self._ask("reopen"),
                on_retry=lambda: self._ask("reopen"),
            )
            return SETUP, app, default_port

        try:
            resolved = vault_mod.resolve_root()
        except VaultError:
            return setup(None)

        if not resolved.path.is_dir():
            return setup(
                setup_app_mod.Problem(
                    setup_app_mod.FOLDER_MISSING,
                    f"Your record folder was not found at {resolved.path}. If it is on "
                    f"a drive that is not plugged in, or your sync app has not "
                    f"finished downloading it, try again once it is there. If it has "
                    f"moved, choose where it is now.",
                    str(resolved.path),
                )
            )
        try:
            vault = vault_mod.Vault.open(resolved.path)
        except HealthAgentError as exc:
            return setup(setup_app_mod.Problem(setup_app_mod.CANNOT_OPEN, str(exc), str(resolved.path)))

        setup_mod.ensure_identity()
        try:
            identity = device_mod.load()
        except HealthAgentError as exc:
            return setup(setup_app_mod.Problem(setup_app_mod.CANNOT_OPEN, str(exc), str(vault.root)))
        if not identity.machine_matches:
            return setup(
                setup_app_mod.Problem(
                    setup_app_mod.IDENTITY_ELSEWHERE,
                    identity.mismatch_reason() or "",
                    str(vault.root),
                )
            )

        from ..server.app import create_app  # noqa: PLC0415 - heavy, and only once a record opens

        try:
            app = create_app(vault)
        except HealthAgentError as exc:
            return setup(setup_app_mod.Problem(setup_app_mod.CANNOT_OPEN, str(exc), str(vault.root)))
        self.vault, self.app = vault, app
        return RECORD, app, self._port_override or previous_port or vault.config.port

    def _stop_server(self) -> None:
        served, self._served = self._served, None
        if served is not None:
            served.stop()

    def _shutdown(self) -> None:
        self._stop_server()
        # The server's own shutdown stops the reader. This is the second line,
        # for a reader started outside a server's lifespan.
        running = supervisor.install(None)
        if running is not None:
            running.shutdown()
        self.instance.release()
        self._set_mode(STOPPED)
        self.stopped.set()

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        try:
            self.on_change()
        except Exception:  # noqa: BLE001 - a tray that failed to redraw is not a reason to stop
            log.exception("the tray did not update")


def _signals_quit(controller: Controller) -> None:
    """SIGTERM and SIGINT quit the app, even while the tray's event loop holds the main thread.

    A Python signal handler only runs when the main thread next executes Python,
    and a native tray loop may not return for a long time. The wakeup fd is
    written by the C-level handler the moment the signal arrives, so a thread
    watching it can start the quit at once.
    """
    if threading.current_thread() is not threading.main_thread() or sys.platform == "win32":
        return
    reader, writer = socket.socketpair()
    writer.setblocking(False)
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: None)
    signal.set_wakeup_fd(writer.fileno(), warn_on_full_buffer=False)

    def watch() -> None:
        while True:
            try:
                data = reader.recv(16)
            except OSError:
                return
            if data:
                controller.quit()
                return

    threading.Thread(target=watch, name="health-agent-signals", daemon=True).start()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        # Only ever printed by --help, on a terminal.
        prog=distribution.for_terminal("health-agent app") or None,
        description=(
            "Run the record as a desktop app: the server on 127.0.0.1, your browser "
            "opened at it, and an icon in the menu bar or system tray."
        ),
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open the browser when the app starts"
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="run without a tray icon; Ctrl-C quits",
    )
    parser.add_argument(
        "--vault",
        default=None,
        help=(
            "open this record folder instead of the one this computer remembers "
            "(for trying the app on a demo record)"
        ),
    )
    parser.add_argument("--port", type=int, default=None, help=argparse.SUPPRESS)
    # For the release workflow, which has no screen and no person to press Quit.
    parser.add_argument("--selftest", default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.vault:
        # Resolved the way every command resolves one: HEALTH_VAULT outranks the
        # pointer, so a demo opened this way never replaces the remembered folder.
        os.environ[vault_mod.ENV_VAULT] = str(Path(args.vault).expanduser().resolve())
    written_to = logs_mod.to_file(replace_streams=distribution.packaged())
    if not distribution.packaged():
        print(f"log       {written_to}", flush=True)

    instance = Instance()
    if not instance.acquire():
        # Another app on this computer holds the lock: it is the running one.
        address = None
        for _ in range(40):
            address = instance.running_address()
            if address:
                break
            time.sleep(0.25)
        if address and not args.no_browser:
            webbrowser.open(address)
        return 0

    controller = Controller(
        open_browser=not args.no_browser, port=args.port, instance=instance
    )

    if args.selftest:
        from . import selftest  # noqa: PLC0415

        return selftest.run(controller, args.selftest)

    _signals_quit(controller)
    if args.no_tray:
        controller.start()
        controller.wait()
        return 0

    from . import tray  # noqa: PLC0415 - the only import of the tray library

    return tray.run(controller)
