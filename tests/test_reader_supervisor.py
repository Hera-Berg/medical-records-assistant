"""The reader's process: identity before the key, crashes, sleep, one per machine.

Every test runs a real child process — :mod:`tests.fixtures.fake_llama_server`,
which speaks the surface the pinned llama-server was observed to speak — so
what is tested is the supervisor's handling of a process, not of a mock. The
real binary and model are exercised by ``health-agent eval`` and by hand; they
are 3.4 GB and have no place in the suite.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from agent.errors import ReaderUnavailable
from agent.llm.client import Client
from agent.extract import probe as probe_mod
from agent.runtime import manifest, states, supervisor
from agent.runtime.store import Store

FAKE = Path(__file__).parent / "fixtures" / "fake_llama_server.py"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process handling")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def fake_launcher(store, platform, port, ctx, model=None):
    return supervisor.Launch(
        argv=[sys.executable, str(FAKE), "--host", "127.0.0.1", "--port", str(port),
              "--alias", manifest.ALIAS],
        cwd=FAKE.parent,
        binary=Path(sys.executable),
    )


@pytest.fixture
def ready_store(tmp_path, monkeypatch):
    """A store that says every file is here, without 3.4 GB of them."""
    store = Store(root=tmp_path / "data-home")
    monkeypatch.setattr(Store, "is_ready", lambda self, bundles: True)
    monkeypatch.setattr(Store, "prepare", lambda self, bundles: None)
    monkeypatch.setattr(Store, "missing", lambda self, bundles: [])
    return store


def make_reader(store, **kwargs) -> supervisor.Reader:
    kwargs.setdefault("launcher", fake_launcher)
    kwargs.setdefault("platform", "linux-x64")
    kwargs.setdefault("start_timeout_s", 20.0)
    kwargs.setdefault("restart_backoff_s", (0.1, 0.1, 0.1))
    reader = supervisor.Reader(store, **kwargs)
    supervisor.install(reader)
    return reader


def wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_not_downloaded_is_reported_and_starts_nothing(tmp_path):
    reader = supervisor.Reader(Store(root=tmp_path / "empty"), platform="linux-x64")
    supervisor.install(reader)
    assert reader.status().state == states.NOT_DOWNLOADED
    with pytest.raises(ReaderUnavailable) as caught:
        reader.ensure_ready()
    assert caught.value.reason == "not-downloaded"
    assert reader._proc is None


def test_an_unsupported_machine_says_so(tmp_path):
    reader = supervisor.Reader(Store(root=tmp_path / "empty"), platform="")
    assert reader.status().reason == "unsupported-platform"


def test_ready_reader_hands_its_key_only_to_its_own_port(ready_store):
    reader = make_reader(ready_store)
    reader.ensure_ready()
    settings = reader.settings()
    assert settings.endpoint.host == "127.0.0.1"
    assert settings.endpoint.port != 8080
    assert settings.model == manifest.ALIAS

    credential = reader.credential_for(settings.endpoint.port)
    assert len(credential.value) >= 32
    with pytest.raises(ReaderUnavailable):
        reader.credential_for(settings.endpoint.port + 1)


def test_the_key_is_never_on_the_command_line_or_on_disk(ready_store, tmp_path):
    reader = make_reader(ready_store)
    reader.ensure_ready()
    key = reader.credential_for(reader.settings().endpoint.port).value
    pid = reader.status().pid
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes() if Path(f"/proc/{pid}").exists() else b""
    assert key.encode() not in cmdline
    for path in ready_store.root.rglob("*"):
        if path.is_file():
            assert key.encode() not in path.read_bytes(), path


def test_inherited_llama_variables_are_stripped(ready_store, tmp_path, monkeypatch):
    dump = tmp_path / "env.json"
    monkeypatch.setenv("LLAMA_ARG_UI", "1")
    monkeypatch.setenv("LLAMA_ARG_LOG_PROMPTS_DIR", str(tmp_path))
    monkeypatch.setenv("FAKE_ENV_DUMP", str(dump))
    reader = make_reader(ready_store)
    reader.ensure_ready()
    names = json.loads(dump.read_text())
    assert "LLAMA_ARG_UI" not in names and "LLAMA_ARG_LOG_PROMPTS_DIR" not in names
    assert "LLAMA_API_KEY" in names


def test_the_client_uses_the_readers_key_and_never_the_keychain(ready_store, monkeypatch):
    from agent.llm import credentials as credentials_mod

    remote_key = "remote-box-key-that-must-not-reach-loopback"
    monkeypatch.setattr(
        credentials_mod, "_from_keychain",
        lambda: credentials_mod.Credential(remote_key, credentials_mod.SOURCE_KEYCHAIN),
    )
    reader = make_reader(ready_store)
    reader.ensure_ready()
    settings = reader.settings()
    seen: list[str] = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"data": [{"id": manifest.ALIAS}]})

    client = Client(
        settings,
        transport=httpx.MockTransport(spy),
        credential=lambda: reader.credential_for(settings.endpoint.port),
    )
    client.list_models()
    assert seen and all(remote_key not in header for header in seen)


def test_the_full_probe_passes_against_the_reader(ready_store):
    reader = make_reader(ready_store)
    reader.ensure_ready()
    settings = reader.settings()
    with Client(
        settings,
        credential=lambda: reader.credential_for(settings.endpoint.port),
        runtime=reader.runtime_facts(),
    ) as client:
        report = probe_mod.run(client)
    assert report.ok, [check.describe() for check in report.checks]
    # Tailscale Funnel is about a box on a tailnet, not a process on loopback.
    assert not any("Funnel" in note for note in report.notes)


def test_the_probe_text_is_large_enough_to_read():
    """At Pillow's 11-pixel default the real 4B reader read "Z07 VERIFY 42"."""
    import io

    from PIL import Image

    image = Image.open(io.BytesIO(probe_mod.probe_image()))
    assert image.size == (640, 200)
    rows = [y for y in range(image.height) if any(image.getpixel((x, y)) != (255, 255, 255) for x in range(0, image.width, 2))]
    assert rows and rows[-1] - rows[0] >= 30, "glyphs at least ~30 pixels tall"


class Recorder(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Recorder.seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_something_else_on_the_port_is_never_sent_the_key(ready_store, monkeypatch):
    """Identity before the key. A foreign server holds the port; the child lies.

    The child prints the listening line for a port it never bound, and a foreign
    server answers ``/health`` there. The socket belongs to another pid, so the
    reader stops without a single request carrying a key reaching the port.
    """
    Recorder.seen = []
    foreign: dict = {}

    def lying_launcher(store, platform, port, ctx, model=None):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Recorder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        foreign["server"] = server
        script = (
            "import sys,time;"
            f"print('main: server is listening on http://127.0.0.1:{port} - starting', flush=True);"
            "time.sleep(60)"
        )
        return supervisor.Launch([sys.executable, "-c", script], FAKE.parent, Path(sys.executable))

    if not supervisor._socket_owner_known():
        pytest.skip("socket ownership is only checkable on Linux")
    reader = make_reader(ready_store, launcher=lying_launcher)
    with pytest.raises(ReaderUnavailable) as caught:
        reader.ensure_ready()
    foreign["server"].shutdown()
    assert caught.value.reason == "not-its-port"
    assert Recorder.seen, "the foreign server was reached for /health"
    assert all(entry["auth"] is None for entry in Recorder.seen)


@pytest.mark.parametrize(
    "variable, value, reason",
    [
        ("FAKE_BUILD", "b1-deadbeef", "wrong-build"),
        ("FAKE_ALIAS", "some-other-model", "wrong-build"),
        ("FAKE_VISION", "0", "no-vision"),
    ],
)
def test_a_reader_that_is_not_the_pinned_one_is_stopped(ready_store, monkeypatch, variable, value, reason):
    monkeypatch.setenv(variable, value)
    reader = make_reader(ready_store)
    with pytest.raises(ReaderUnavailable) as caught:
        reader.ensure_ready()
    assert caught.value.reason == reason
    assert reader.status().state == states.STOPPED
    assert reader._proc is None


def test_a_crash_restarts_and_three_stop_it(ready_store, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT_AFTER", "0.6")
    reader = make_reader(ready_store)
    reader.ensure_ready()
    assert wait_for(lambda: reader.status().reason == "crashed-repeatedly", timeout=30)
    status = reader.status()
    assert status.state == states.STOPPED and status.launches == 3
    with pytest.raises(ReaderUnavailable):
        reader.ensure_ready()

    monkeypatch.delenv("FAKE_EXIT_AFTER")
    reader.retry()
    assert wait_for(lambda: reader.status().state == states.READY)


def test_a_killed_reader_is_reported_as_out_of_memory(ready_store):
    reader = make_reader(ready_store)
    reader.ensure_ready()
    os.kill(reader.status().pid, 9)
    assert wait_for(lambda: reader.status().reason == "out-of-memory")
    assert reader.status().state == states.STOPPED


def test_it_sleeps_when_idle_and_wakes_on_demand(ready_store):
    clock = Clock()
    reader = make_reader(ready_store, clock=clock, sleep_after_minutes=15)
    reader.ensure_ready()
    first_pid = reader.status().pid

    clock.now += 14 * 60
    time.sleep(1.2)
    assert reader.status().state == states.READY

    clock.now += 2 * 60
    assert wait_for(lambda: reader.status().state == states.SLEEPING)
    assert reader.status().reason == "sleeping"
    assert reader._proc is None

    reader.ensure_ready()
    assert reader.status().state == states.READY
    assert reader.status().pid != first_pid


def test_it_never_sleeps_with_work_queued_or_a_job_running(ready_store):
    clock = Clock()
    reader = make_reader(ready_store, clock=clock, sleep_after_minutes=1)
    reader.ensure_ready()

    reader.has_work = lambda: True
    clock.now += 5 * 60
    time.sleep(1.2)
    assert reader.status().state == states.READY

    reader.has_work = lambda: False
    with reader.in_use():
        clock.now += 5 * 60
        time.sleep(1.2)
        assert reader.status().state == states.READY
    clock.now += 5 * 60
    assert wait_for(lambda: reader.status().state == states.SLEEPING)


def test_zero_minutes_means_it_never_sleeps(ready_store):
    clock = Clock()
    reader = make_reader(ready_store, clock=clock, sleep_after_minutes=0)
    reader.ensure_ready()
    clock.now += 10 * 24 * 3600
    time.sleep(1.2)
    assert reader.status().state == states.READY


def test_a_second_process_on_this_machine_is_refused(ready_store):
    first = make_reader(ready_store)
    first.ensure_ready()
    second = supervisor.Reader(ready_store, platform="linux-x64", launcher=fake_launcher)
    try:
        with pytest.raises(ReaderUnavailable) as caught:
            second.ensure_ready(timeout=10)
        assert caught.value.reason == "in-use-elsewhere"
    finally:
        second.shutdown()


def test_shutdown_stops_the_process_and_removes_the_pidfile(ready_store):
    reader = make_reader(ready_store)
    reader.ensure_ready()
    pid = reader.status().pid
    reader.shutdown()
    assert wait_for(lambda: not Path(f"/proc/{pid}").exists() or _zombie(pid))
    assert not (ready_store.root / supervisor.PID_FILENAME).exists()


def _zombie(pid: int) -> bool:
    try:
        return " Z " in Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return True


def test_an_orphan_left_by_a_dead_process_is_stopped_on_the_next_start(ready_store):
    import subprocess

    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (ready_store.root).mkdir(parents=True, exist_ok=True)
    (ready_store.root / supervisor.PID_FILENAME).write_text(
        json.dumps({"pid": orphan.pid, "binary": sys.executable})
    )
    if not sys.platform.startswith("linux"):
        orphan.kill()
        pytest.skip("orphan identification is checked on Linux")
    reader = make_reader(ready_store)
    reader.ensure_ready()
    assert orphan.wait(timeout=15) is not None


def test_a_stranger_with_a_reused_pid_is_left_alone(ready_store):
    import subprocess

    stranger = subprocess.Popen(["sleep", "30"])
    ready_store.root.mkdir(parents=True, exist_ok=True)
    (ready_store.root / supervisor.PID_FILENAME).write_text(
        json.dumps({"pid": stranger.pid, "binary": "/opt/health-agent/llama-server"})
    )
    reader = make_reader(ready_store)
    reader.ensure_ready()
    assert stranger.poll() is None
    stranger.kill()


def test_the_status_says_nothing_the_process_printed(ready_store):
    reader = make_reader(ready_store)
    reader.ensure_ready()
    body = reader.status().to_dict()
    assert body["message"] == states.MESSAGES["ready"]
    assert set(body) == {"state", "reason", "message", "launches", "log_path"}


def test_threads_are_stated_not_left_to_the_default():
    """llama-server's default used 2 of 14 threads on a hybrid Intel laptop chip."""
    assert supervisor.thread_counts(14) == (12, 14)
    assert supervisor.thread_counts(2) == (2, 2)
    assert supervisor.thread_counts(4) == (2, 4)
