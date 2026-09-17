"""The launcher: first run to a working record, and Quit taking the reader with it.

Real servers on real sockets, and — for the reader — a real child process, the
fake llama-server the supervisor's own tests use. What is not exercised here is
the tray library itself, which needs a desktop session; the controller it calls
is everything behind the four menu items.
"""

from __future__ import annotations

import socket
import sys
import time

import httpx
import pytest

from agent import device as device_mod
from agent import firstrun
from agent import vault as vault_mod
from agent.app import launcher as launcher_mod
from agent.app import setup_app as setup_app_mod
from agent.app.instance import Instance
from agent.runtime import choice as choice_mod
from agent.runtime import supervisor

from .test_reader_supervisor import make_reader, ready_store  # noqa: F401 - fixture


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _until(predicate, seconds=30.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def home(tmp_path):
    folder = tmp_path / "home"
    (folder / "Documents").mkdir(parents=True)
    return folder


@pytest.fixture
def opened():
    return []


@pytest.fixture
def controller(opened, tmp_path):
    made = launcher_mod.Controller(
        open_browser=True,
        browser=opened.append,
        port=_free_port(),
        instance=Instance(tmp_path / "instance"),
    )
    assert made.instance.acquire()
    yield made
    made.quit()
    made.wait(timeout=60)


def _client(controller):
    base = controller.url.rstrip("/")
    return httpx.Client(
        base_url=base, headers={"origin": base, "sec-fetch-site": "same-origin"}, timeout=30
    )


def test_first_run_asks_for_a_folder_then_opens_the_record(controller, opened, home):
    controller.start()
    assert _until(lambda: controller.mode == launcher_mod.SETUP)
    assert opened == [controller.url], "the browser is opened once, at the app"
    assert controller.status_line() == "Finish setting up in your browser"
    assert controller.folder() is None
    assert controller.folder_label() == "Open the folder — not chosen yet"

    with _client(controller) as http:
        setup = http.get("/api/setup").json()
        assert setup["stage"] == setup_app_mod.CHOOSE
        assert http.get("/api/health").status_code == 404, "no record is served yet"

        parent, name = str(home / "Documents"), setup["default_name"]
        plan = http.post("/api/setup/examine", json={"parent": parent, "name": name}).json()
        chosen = http.post(
            "/api/setup/choose", json={"parent": parent, "name": name, "action": plan["action"]}
        )
        assert chosen.status_code == 200, chosen.text

        assert _until(lambda: controller.mode == launcher_mod.RECORD)
        health = http.get("/api/health").json()
        assert health["welcome"] is True
        assert controller.folder() == home / "Documents" / "Health record"
        assert controller.status_line().startswith("Reader: ")
        assert http.get("/api/setup").status_code == 404, "setup is over"

        assert http.post("/api/welcome/done").status_code == 200
        assert http.get("/api/health").json()["welcome"] is False
    assert opened == [controller.url], "changing mode does not open a second tab"


def test_a_cross_site_page_cannot_choose_the_folder(controller, home):
    controller.start()
    assert _until(lambda: controller.mode == launcher_mod.SETUP)
    refused = httpx.post(
        controller.url + "api/setup/choose",
        json={"parent": str(home / "Documents"), "name": "Health record", "action": "create"},
        headers={"origin": "https://evil.example"},
    )
    assert refused.status_code == 403
    assert not (home / "Documents" / "Health record").exists()


def test_quit_stops_the_server_and_releases_the_app(controller):
    controller.start()
    assert _until(lambda: controller.mode == launcher_mod.SETUP)
    controller.quit()
    assert controller.wait(timeout=60)
    assert controller.mode == launcher_mod.STOPPED
    assert launcher_mod.port_free(controller.port)
    other = Instance(controller.instance.home)
    assert other.acquire(), "the lock is released"
    other.release()


def test_a_second_launch_finds_the_first(controller):
    controller.start()
    assert _until(lambda: controller.mode == launcher_mod.SETUP)
    second = Instance(controller.instance.home)
    assert not second.acquire()
    assert second.running_address() == controller.url


def test_a_taken_port_says_so_and_opens_nothing(controller, opened):
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", controller.port))
        squatter.listen()
        controller.start()
        assert _until(lambda: controller.mode == launcher_mod.PORT_BUSY)
        assert opened == [], "a browser opened at a stranger's server is how a record gets typed into it"
        assert not controller.can_open()
        assert controller.open_label() == f"Open — another program is using port {controller.port}"


def test_a_missing_folder_is_a_problem_page_not_a_crash(controller, home):
    vault_mod.write_pointer(home / "Documents" / "Unplugged drive")
    controller.start()
    assert _until(lambda: controller.mode == launcher_mod.SETUP)
    assert controller.problem.code == setup_app_mod.FOLDER_MISSING
    assert controller.status_line() == "Needs attention — open to see why"
    with _client(controller) as http:
        problem = http.get("/api/setup").json()["problem"]
    assert "was not found" in problem["message"]


def test_an_identity_from_another_computer_is_asked_about(controller, vault_root):
    import json

    vault_mod.write_pointer(vault_root)
    device_mod.issue(label="elsewhere")
    path = device_mod.identity_path()
    data = json.loads(path.read_text())
    data["hostname"] = "some-other-machine"
    path.write_text(json.dumps(data))

    controller.start()
    assert _until(lambda: controller.mode == launcher_mod.SETUP)
    assert controller.problem.code == setup_app_mod.IDENTITY_ELSEWHERE

    with _client(controller) as http:
        assert http.post("/api/setup/new-identity").status_code == 200
    assert _until(lambda: controller.mode == launcher_mod.RECORD)
    assert device_mod.load().machine_matches


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process handling")
def test_quit_stops_the_reader(controller, vault_root, ready_store):  # noqa: F811
    """The thing to test for on every platform: no 3.5 GB orphan after Quit."""
    vault_mod.write_pointer(vault_root)
    device_mod.issue(label="this-machine")
    choice_mod.save(choice_mod.THIS_COMPUTER)
    reader = make_reader(ready_store)

    controller.start()
    assert _until(lambda: controller.mode == launcher_mod.RECORD)
    assert supervisor.get(controller.vault) is reader

    reader.ensure_ready(timeout=30)
    process = reader._proc
    assert process is not None and process.poll() is None
    assert controller.status_line() == "Reader: Reading on this computer"

    controller.quit()
    assert controller.wait(timeout=60)
    assert _until(lambda: process.poll() is not None, 20), "the reader outlived Quit"
    assert supervisor.install(None) is None, "the server's shutdown let go of the reader"


def test_quit_before_start_still_stops(controller):
    controller.quit()
    assert controller.wait(timeout=5)
    assert controller.mode == launcher_mod.STOPPED


def test_first_run_marker_lives_outside_the_vault():
    assert firstrun.path().parent == device_mod.config_home()
