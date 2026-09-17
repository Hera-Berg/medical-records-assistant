"""The reader does not outlive an app that was killed rather than quit.

macOS has no ``PR_SET_PDEATHSIG`` and no job objects, so a shell loop watches
the app's pid and stops the reader when the app is gone. The loop is plain
POSIX sh, so it is exercised here on Linux against real processes: a stand-in
"app" that is SIGKILLed, and a stand-in "reader" launched by absolute path.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import time

import pytest

from agent.runtime import supervisor

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell watcher")


def _wait_gone(proc: subprocess.Popen, seconds: float) -> bool:
    """Every process here is this test's child, so ``poll`` reaps and reports it
    — which also holds on macOS, where there is no /proc to tell a zombie apart."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.2)
    return False


@pytest.fixture
def reader_binary(tmp_path):
    """A copy of `sleep` at an absolute path of its own, standing in for llama-server."""
    source = shutil.which("sleep")
    target = tmp_path / "llama-server"
    shutil.copy2(source, target)
    return target


def test_a_killed_app_takes_the_reader_with_it(reader_binary):
    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    reader = subprocess.Popen([str(reader_binary), "600"], start_new_session=True)
    watcher = supervisor.watch_parent(reader.pid, str(reader_binary), parent_pid=app.pid)
    try:
        time.sleep(0.5)
        assert reader.poll() is None
        app.send_signal(signal.SIGKILL)
        app.wait(timeout=10)
        assert _wait_gone(reader, 10), "the reader survived the app being killed"
        watcher.wait(timeout=10)
    finally:
        for proc in (app, reader, watcher):
            if proc.poll() is None:
                proc.kill()


def test_the_watcher_leaves_when_the_reader_stops_first(reader_binary):
    reader = subprocess.Popen([str(reader_binary), "600"])
    watcher = supervisor.watch_parent(reader.pid, str(reader_binary))
    try:
        reader.terminate()
        reader.wait(timeout=10)
        assert watcher.wait(timeout=10) == 0
    finally:
        for proc in (reader, watcher):
            if proc.poll() is None:
                proc.kill()


def test_a_reused_pid_running_something_else_is_left_alone(tmp_path, reader_binary):
    """The pid may belong to a stranger by the time the app is gone."""
    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    stranger = subprocess.Popen([shutil.which("sleep"), "600"], start_new_session=True)
    watcher = supervisor.watch_parent(stranger.pid, str(reader_binary), parent_pid=app.pid)
    try:
        app.send_signal(signal.SIGKILL)
        app.wait(timeout=10)
        watcher.wait(timeout=15)
        assert stranger.poll() is None, "a process that is not the reader was signalled"
    finally:
        for proc in (app, stranger, watcher):
            if proc.poll() is None:
                proc.kill()
