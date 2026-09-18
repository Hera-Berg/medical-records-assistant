"""Whether this computer still has the second first-run question to answer.

The first run asks two things: where the record folder lives, and which computer
reads documents. The first is answered before the record can open at all. The
second is asked once it has, on the record's own screens, because the reader's
choices — the model list, the one-time download, connecting to another
computer — are the Settings screen's and are not built twice.

So choosing a folder leaves a marker, and the welcome screen removes it. The
marker is this machine's, beside the device identity and outside the vault:
the second computer to join a synced record has to be asked too, and a marker
inside the folder would have been removed by the first.
"""

from __future__ import annotations
from . import files

import contextlib
from pathlib import Path

from . import device as device_mod

FILENAME = "welcome-pending"


def path() -> Path:
    return device_mod.config_home() / FILENAME


def pending() -> bool:
    return path().is_file()


def mark() -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    files.write_text(
        target,
        "The health record app asks which computer reads documents the next time "
        "it opens, then removes this file.\n",
    )


def finish() -> None:
    with contextlib.suppress(FileNotFoundError):
        path().unlink()
