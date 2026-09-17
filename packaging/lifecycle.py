"""Run the frozen app through its self-tests, and check from outside that nothing survives it.

The release workflow runs this on every platform it builds. Each check is a
fact recorded in ``results.json``, which the release notes are written from:

``first-run``      the app starts, asks for a folder, creates the default one,
                   serves the record, and quits. Every bundled library loads.
``reader-quit``    the reader is downloaded through the app's own routes,
                   started, and passes the full startup probe, vision included;
                   then Quit, and the reader's process is gone.
``reader-killed``  the same, but the app is killed instead — SIGKILL, or
                   ``taskkill /F`` — and the reader's process is gone anyway.

A check that could not run says why. Nothing here reads a result as better than
it was: a reader that did not start is a failed check, not a skipped one.

    python packaging/lifecycle.py --app PATH --work DIR [--reader] --out results.json
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PORT = 7777
READER_NAMES = ("llama-server", "llama-server.exe")


def alive(pid: int) -> bool:
    if os.name == "nt":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, check=False,
        ).stdout
        return f'"{pid}"' in listing
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def readers_running() -> list[str]:
    """Every process on the machine whose name is the reader's, however it started."""
    if os.name == "nt":
        listing = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, check=False,
        ).stdout
        return [line for line in listing.splitlines() if "llama-server" in line]
    listing = subprocess.run(["ps", "-A", "-o", "pid=,comm="], capture_output=True, text=True, check=False).stdout
    return [line.strip() for line in listing.splitlines() if line.strip().endswith(READER_NAMES)]


def gone_within(pid: int, seconds: float) -> float | None:
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if not alive(pid):
            return round(time.monotonic() - started, 1)
        time.sleep(0.25)
    return None


class Run:
    def __init__(self, app: Path, work: Path, name: str, data_home: Path) -> None:
        self.app, self.name = app, name
        self.root = work / name
        self.report = self.root / "report.json"
        home = self.root / "home"
        (home / "Documents").mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ)
        self.env.update(
            HOME=str(home),
            USERPROFILE=str(home),
            HEALTH_AGENT_CONFIG_HOME=str(self.root / "config"),
            HEALTH_AGENT_DATA_HOME=str(data_home),
            HEALTH_AGENT_SELFTEST_REPORT=str(self.report),
        )

    def start(self, mode: str) -> subprocess.Popen:
        return subprocess.Popen(
            [str(self.app), "--no-browser", "--port", str(PORT), "--selftest", mode],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def read(self) -> dict:
        try:
            return json.loads(self.report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


def first_run(app: Path, work: Path, data_home: Path) -> dict:
    run = Run(app, work, "first-run", data_home)
    process = run.start("quit")
    code = process.wait(timeout=600)
    report = run.read()
    return {
        "ok": code == 0 and report.get("ok") is True and report.get("packaged") is True,
        "exit_code": code,
        "report": report,
    }


def reader_quit(app: Path, work: Path, data_home: Path) -> dict:
    run = Run(app, work, "reader-quit", data_home)
    process = run.start("reader-quit")
    code = process.wait(timeout=3 * 3600)
    report = run.read()
    pid = report.get("reader_pid")
    result = {"exit_code": code, "report": report, "reader_pid": pid}
    if not report.get("ok"):
        result.update(ok=False, why=report.get("error", "the self-test did not finish"))
        return result
    seconds = gone_within(int(pid), 15) if pid else None
    result.update(
        ok=code == 0 and pid is not None and seconds is not None and not readers_running(),
        reader_gone_after_s=seconds,
        readers_left=readers_running(),
    )
    return result


def reader_killed(app: Path, work: Path, data_home: Path) -> dict:
    run = Run(app, work, "reader-killed", data_home)
    process = run.start("reader-hold")
    deadline = time.monotonic() + 3 * 3600
    report: dict = {}
    while time.monotonic() < deadline and process.poll() is None:
        report = run.read()
        if report.get("holding") or report.get("error"):
            break
        time.sleep(1)
    pid = report.get("reader_pid")
    result: dict = {"report": report, "reader_pid": pid}
    if not report.get("holding") or not pid:
        process.kill()
        result.update(ok=False, why=report.get("error", "the reader never started"))
        return result

    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(process.pid)], check=False, capture_output=True)
        result["killed_with"] = "taskkill /F"
    else:
        os.kill(process.pid, signal.SIGKILL)
        result["killed_with"] = "SIGKILL"
    process.wait(timeout=30)
    seconds = gone_within(int(pid), 30)
    result.update(
        ok=seconds is not None and not readers_running(),
        reader_gone_after_s=seconds,
        readers_left=readers_running(),
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--reader", action="store_true")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    data_home = args.work / "data-home"
    results: dict = {"platform": sys.platform, "checks": {}}
    results["checks"]["first-run"] = first_run(args.app, args.work, data_home)
    if args.reader:
        results["checks"]["reader-quit"] = reader_quit(args.app, args.work, data_home)
        results["checks"]["reader-killed"] = reader_killed(args.app, args.work, data_home)
    else:
        results["checks"]["reader-quit"] = {"ok": None, "why": "not run in this job"}
        results["checks"]["reader-killed"] = {"ok": None, "why": "not run in this job"}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for name, check in results["checks"].items():
        print(f"{name:15} {check.get('ok')}  {check.get('why', '')}")
    return 0 if all(check.get("ok") is not False for check in results["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
