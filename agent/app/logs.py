"""Where the app writes what it would otherwise print.

A windowed app on Windows has no console at all: ``sys.stdout`` is ``None``, and
the first ``print`` anywhere in the program raises. On macOS the output of an app
opened from Finder goes nowhere a person can find. So the app sends both to a
log file in the reader's data directory — outside the vault, where nothing
syncs it — with the key-scrubbing filter installed before the first line.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from ..llm import redaction
from ..runtime import platforms

FILENAME = "app.log"
PREVIOUS = "app.previous.log"


def path() -> Path:
    return platforms.data_home() / "logs" / FILENAME


def to_file(replace_streams: bool) -> Path:
    """Send logging, and the standard streams when asked, to the app's log file."""
    redaction.install()
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        os.replace(target, target.with_name(PREVIOUS))
    handler = logging.FileHandler(target, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    if replace_streams or sys.stdout is None or sys.stderr is None:
        stream = open(target, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives as long as the process
        sys.stdout = stream
        sys.stderr = stream
    return target
