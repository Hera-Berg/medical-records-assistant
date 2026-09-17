"""The app, driven end to end with nobody at the screen. For the release workflow.

The macOS and Windows builds of the reader were pinned and hash-checked and
never run. A person clicking through each platform once is necessary and is not
enough, so the release job runs the **frozen app itself** through this:

``quit``
    First run through the real setup routes over real HTTP, the record's server
    in its place, the page and the guard answering, then the Quit handler the
    tray's Quit item calls.
``reader-quit``
    The same, plus the one-time download through the real routes — the size
    confirmation included — the reader started, and the full startup probe,
    grammar and vision, run against it. Then Quit. The report names the
    reader's pid, and the workflow checks from outside that it is gone.
``reader-hold``
    The same as ``reader-quit`` but never quits: the workflow kills the app
    with SIGKILL or ``taskkill /F`` and checks the reader goes with it.

Nothing here is reachable from the interface. It exists so "quitting stops the
reader" is a result from a run on each platform rather than a sentence.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import httpx

from .. import distribution
from ..runtime import platforms, supervisor
from . import launcher as launcher_mod

ENV_REPORT = "HEALTH_AGENT_SELFTEST_REPORT"
MODES = ("quit", "reader-quit", "reader-hold")

DOWNLOAD_TIMEOUT_S = 60 * 60
READER_TIMEOUT_S = 30 * 60


class Failed(Exception):
    pass


def run(controller: launcher_mod.Controller, mode: str) -> int:
    if mode not in MODES:
        print(f"unknown self-test {mode!r}; expected one of {', '.join(MODES)}")
        return 2
    report: dict[str, Any] = {
        "mode": mode,
        "packaged": distribution.packaged(),
        "platform": platforms.current(),
        "python": sys.version.split()[0],
        "steps": [],
        "ok": False,
    }
    path = Path(os.environ.get(ENV_REPORT) or (platforms.data_home() / "selftest.json"))

    def step(name: str, started: float, detail: str = "") -> None:
        report["steps"].append(
            {"name": name, "seconds": round(time.monotonic() - started, 1), "detail": detail}
        )
        _write(path, report)

    controller.start()
    try:
        _drive(controller, mode, report, step)
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - the report is the result
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    _write(path, report)

    if mode == "reader-hold" and report["ok"]:
        report["holding"] = True
        _write(path, report)
        controller.wait()  # until killed from outside

    started = time.monotonic()
    controller.quit()
    stopped = controller.wait(timeout=120)
    step("quit", started, "stopped" if stopped else "did not stop within 120 seconds")
    report["quit_clean"] = stopped
    _write(path, report)
    return 0 if report["ok"] and stopped else 1


#: Everything the app ships so that no message ever tells its owner to install
#: something. A failed import here is the packaging fault those messages
#: describe, found by the release job instead of by a person.
BUNDLED = ("faster_whisper", "ctranslate2", "onnxruntime", "av", "pdfplumber", "pypdfium2", "keyring", "pystray")


def _bundled(report, step) -> None:
    import importlib  # noqa: PLC0415

    started = time.monotonic()
    missing = []
    for name in BUNDLED:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - pystray raises more than ImportError without a desktop
            if name == "pystray" and sys.platform.startswith("linux"):
                continue  # needs a desktop session on Linux, which a runner does not have
            missing.append(f"{name}: {type(exc).__name__}: {exc}")
    try:
        import keyring  # noqa: PLC0415

        report["keychain"] = type(keyring.get_keyring()).__module__
    except Exception as exc:  # noqa: BLE001
        missing.append(f"keychain backend: {exc}")
    if missing:
        raise Failed("bundled libraries did not load: " + "; ".join(missing))
    step("bundled libraries load", started, ", ".join(BUNDLED))


def _drive(controller, mode, report, step) -> None:
    _bundled(report, step)
    started = time.monotonic()
    _until(lambda: controller.mode != launcher_mod.STARTING, 60, "the app to start")
    if controller.mode != launcher_mod.SETUP:
        raise Failed(f"expected the first run to ask for a folder, but the app is in {controller.mode!r}")
    step("started in setup", started)
    base = controller.url.rstrip("/")
    headers = {"origin": base, "sec-fetch-site": "same-origin"}

    with httpx.Client(base_url=base, headers=headers, timeout=60) as http:
        started = time.monotonic()
        setup = _ok(http.get("/api/setup"))
        chosen = setup["locations"][0]
        if not chosen["recommended"] or chosen["profile"] != "local":
            raise Failed(f"the first location offered is not the local, recommended one: {chosen}")
        plan = _ok(http.post("/api/setup/examine", json={"parent": chosen["parent"], "name": setup["default_name"]}))
        if plan["refusal"]:
            raise Failed(f"the default folder was refused: {plan['refusal']}")
        _ok(
            http.post(
                "/api/setup/choose",
                json={"parent": chosen["parent"], "name": setup["default_name"], "action": plan["action"]},
            )
        )
        _until(lambda: controller.mode == launcher_mod.RECORD, 60, "the record to open")
        step("chose the default folder", started, plan["target"])

        started = time.monotonic()
        health = _ok(http.get("/api/health"))
        if not health["welcome"]:
            raise Failed("the record did not ask which computer reads documents")
        page = http.get("/")
        if page.status_code != 200 or 'id="root"' not in page.text:
            raise Failed(f"the interface was not served: HTTP {page.status_code}")
        rebound = http.get("/api/health", headers={"host": "evil.example"})
        if rebound.status_code != 421:
            raise Failed(f"a rebinding Host header was answered: HTTP {rebound.status_code}")
        step("record served", started)

        if mode.startswith("reader"):
            _reader(controller, http, report, step)


def _reader(controller, http, report, step) -> None:
    started = time.monotonic()
    _ok(http.post("/api/settings/reader", json={"reads_on": "this-computer"}))
    info = _ok(http.get("/api/reader"))
    remaining = int(info["files"]["remaining_bytes"])
    if remaining:
        refused = http.post("/api/reader/download", json={})
        if refused.status_code != 409:
            raise Failed(f"a download without the size confirmation was not refused: HTTP {refused.status_code}")
        _ok(http.post("/api/reader/download", json={"confirm_bytes": remaining}))

        def downloaded() -> bool:
            files = _ok(http.get("/api/reader"))["files"]
            if files["state"] in ("failed", "stopped"):
                raise Failed(f"the download did not finish: {files['state']} — {files.get('message')}")
            return files["state"] == "complete" or (
                int(files["remaining_bytes"]) == 0 and files["state"] == "idle"
            )

        _until(downloaded, DOWNLOAD_TIMEOUT_S, "the reader's files to download and verify")
    step("downloaded and verified", started, f"{remaining:,} bytes")

    started = time.monotonic()
    from ..extract import probe as probe_mod  # noqa: PLC0415
    from ..extract import session  # noqa: PLC0415

    reader = supervisor.get(controller.vault)
    reader.ensure_ready(timeout=READER_TIMEOUT_S)
    status = reader.status()
    report["reader_pid"] = status.pid
    report["reader_log"] = status.log_path
    step("reader started", started, f"pid {status.pid}")

    started = time.monotonic()
    with session.open_client(controller.vault) as client:
        probe = probe_mod.run(client)
    report["probe"] = probe.to_dict()
    if not probe.ok:
        raise Failed(f"the startup probe did not pass: {probe.state}")
    step("probe passed, vision included", started)


def _ok(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 300:
        raise Failed(f"{response.request.method} {response.request.url.path}: HTTP {response.status_code} {response.text[:500]}")
    return response.json()


def _until(condition, seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.5)
    raise Failed(f"timed out after {seconds:.0f} seconds waiting for {what}")


def _write(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
