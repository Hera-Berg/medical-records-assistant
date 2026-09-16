"""Which computer reads this machine's documents. Stored per machine.

``~/.config/health-agent/reader``, beside the device identity, and **never in
``config.toml``**. That file syncs to every machine that shares the folder, so
"read on this computer" written from the laptop would be a false statement on
the desktop — the same reasoning that put the device identity outside the vault.
The remote endpoint's own details stay in ``config.toml``, because the box is
the same box from every machine.

The default depends on the vault: one that already has a ``[models.vlm]`` table
was set up to read on a box, and upgrading must not quietly move it onto a 4B
model on a laptop. Everything else starts on this computer.

The default is **written the first time the server starts** (:func:`settle`),
not merely computed each time. Computed, it would flip on its own the day a
synced ``config.toml`` gained a ``[models.vlm]`` table from another machine — a
person reading on their laptop would find their documents going to a box they
set up for a different computer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import device as device_mod
from ..errors import ConfigError

FILENAME = "reader"

THIS_COMPUTER = "this-computer"
ANOTHER_COMPUTER = "another-computer"
CHOICES = (THIS_COMPUTER, ANOTHER_COMPUTER)

#: How long the reader stays loaded with nothing to do. Long enough that a
#: second photograph a few minutes after the first does not pay for a reload;
#: short enough that an 8 GB laptop gets its memory back while the app sits in
#: the background.
DEFAULT_SLEEP_MINUTES = 15
#: ``0`` means it never sleeps: a real preference on a machine with memory to
#: spare, and one a person has to choose deliberately.
MAX_SLEEP_MINUTES = 24 * 60

LABELS = {
    THIS_COMPUTER: "Read on this computer",
    ANOTHER_COMPUTER: "Read on another computer",
}


@dataclass(frozen=True)
class Choice:
    reads_on: str
    sleep_after_minutes: int = DEFAULT_SLEEP_MINUTES
    #: ``file`` once written; ``default`` when nothing has been written yet.
    source: str = "default"

    @property
    def reads_here(self) -> bool:
        return self.reads_on == THIS_COMPUTER

    def to_dict(self) -> dict[str, Any]:
        return {
            "reads_on": self.reads_on,
            "label": LABELS[self.reads_on],
            "sleep_after_minutes": self.sleep_after_minutes,
            "source": self.source,
        }


def path() -> Path:
    return device_mod.config_home() / FILENAME


def default_for(vault) -> str:
    """Another computer for a vault already set up with a box; this one otherwise."""
    models = vault.config.raw.get("models")
    if isinstance(models, dict) and isinstance(models.get("vlm"), dict):
        return ANOTHER_COMPUTER
    return THIS_COMPUTER


def _read(target: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ConfigError(
            f"{target} could not be read ({exc}). It records which computer reads "
            f"documents on this machine; delete it to go back to the default, or "
            f"choose again in Settings."
        ) from None
    if not isinstance(data, dict):
        raise ConfigError(f"{target} must hold a JSON object; delete it to reset it")
    return data


def validate_minutes(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("sleep_after_minutes must be a whole number of minutes")
    if not 0 <= value <= MAX_SLEEP_MINUTES:
        raise ConfigError(
            f"sleep_after_minutes must be between 0 (never) and {MAX_SLEEP_MINUTES}"
        )
    return value


def load(vault) -> Choice:
    """This machine's choice, or the vault's default if none is written yet."""
    data = _read(path())
    if data is None:
        return Choice(reads_on=default_for(vault))
    reads_on = data.get("reads_on")
    if reads_on not in CHOICES:
        raise ConfigError(
            f"{path()} names {reads_on!r}, which is not a place documents can be read. "
            f"Expected one of {', '.join(CHOICES)}."
        )
    minutes = validate_minutes(data.get("sleep_after_minutes", DEFAULT_SLEEP_MINUTES))
    return Choice(reads_on=reads_on, sleep_after_minutes=minutes, source="file")


def save(reads_on: str, sleep_after_minutes: int = DEFAULT_SLEEP_MINUTES) -> Choice:
    """Write this machine's choice atomically, readable only by its owner."""
    if reads_on not in CHOICES:
        raise ConfigError(f"unknown reader choice {reads_on!r}")
    minutes = validate_minutes(sleep_after_minutes)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {"reads_on": reads_on, "sleep_after_minutes": minutes}, indent=2, sort_keys=True
    ) + "\n"
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)
    os.replace(temporary, target)
    return Choice(reads_on=reads_on, sleep_after_minutes=minutes, source="file")


def settle(vault) -> Choice:
    """Write the default if nothing is written yet. Called when the server starts.

    A demo vault never writes it. It may *read* on this computer like any vault,
    but the choice is the machine's, and a demo has no ``[models.vlm]`` table: a
    demo served first would write "this computer" for the whole machine and
    override the "another computer" default a real vault set up with a box is
    owed.
    """
    current = load(vault)
    if current.source == "file" or vault.is_demo:
        return current
    return save(current.reads_on, current.sleep_after_minutes)
