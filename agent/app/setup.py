"""Choosing where the record folder lives: create a new one, or join one that exists.

Two acts, told apart by what is already there:

- **Create.** The folder does not exist, or is empty. It is made, laid out, and
  given a ``config.toml`` a person can read. Nothing else is invented.
- **Join.** The folder already holds a ``config.toml`` — the second computer
  opening a record kept in Dropbox, say. Nothing in it is written. This machine
  records where the folder is and gets a device identity of its own, which is
  what keeps two computers from writing to the same event file.

A folder with other things in it and no ``config.toml`` is refused rather than
filled: the record is its own folder, and scattering ``raw/`` and ``events/``
among somebody's tax returns is a mess nobody can undo by hand.

Every refusal is a sentence the setup screen shows beside the folder it is
about. :func:`examine` writes nothing, so the screen can say what *will* happen
— including the exact settings file — before the person agrees to it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config as config_mod
from .. import device as device_mod
from .. import firstrun
from .. import vault as vault_mod
from ..config import CONFIG_FILENAME, SyncProfile
from ..errors import ConfigError, HealthAgentError
from ..runtime import platforms
from . import locations as locations_mod

CREATE = "create"
JOIN = "join"

#: Files an operating system or sync client drops into any folder it touches.
#: A folder holding only these is empty as far as a person is concerned.
IGNORABLE = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", ".localized", "Icon\r"})


@dataclass(frozen=True)
class Plan:
    """What choosing a folder would do, worked out without touching anything."""

    target: Path
    action: str | None
    profile: SyncProfile
    refusal: str | None = None
    config_text: str | None = None

    @property
    def ok(self) -> bool:
        return self.refusal is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": str(self.target),
            "action": self.action,
            "profile": self.profile.value,
            "profile_label": self.profile.label,
            "warning": self.profile.setup_warning,
            "refusal": self.refusal,
            "config_text": self.config_text,
        }


def examine(parent: str, name: str, locations: list[locations_mod.Location] | None = None) -> Plan:
    """What would happen if the record lived at *parent*/*name*. Writes nothing."""
    locations = locations if locations is not None else locations_mod.detect()
    name = (name or "").strip()
    raw_parent = (parent or "").strip()

    def refuse(target: Path, sentence: str) -> Plan:
        return Plan(target=target, action=None, profile=SyncProfile.LOCAL, refusal=sentence)

    if not raw_parent:
        return refuse(Path(), "Choose a folder for the record to go in.")
    base = Path(raw_parent).expanduser()
    if not base.is_absolute():
        return refuse(
            base,
            "Type the folder's whole path, starting from the top of the disk — "
            "for example the path your file manager shows for it.",
        )
    if not name:
        return refuse(base, "Give the record's folder a name.")
    if name in (".", "..") or any(sep in name for sep in ("/", "\\")) or name != name.strip("."):
        return refuse(base / name, "A folder name cannot contain slashes or start or end with a dot.")

    target = base / name
    profile = locations_mod.profile_for(target, locations)

    if platforms.is_inside(target, platforms.data_home()) or platforms.is_inside(
        platforms.data_home(), target
    ):
        return refuse(
            target,
            "That is where the app keeps the reader's own files. The record needs a "
            "folder of its own somewhere else.",
        )
    if platforms.is_inside(target, device_mod.config_home()):
        return refuse(
            target,
            "That is where the app keeps this computer's own settings. The record "
            "needs a folder of its own somewhere else.",
        )

    if target.exists() and not target.is_dir():
        return refuse(target, f"{target} is a file, not a folder. Choose another name.")

    if (target / CONFIG_FILENAME).is_file():
        try:
            existing = config_mod.load(target / CONFIG_FILENAME)
        except ConfigError as exc:
            return refuse(
                target,
                f"This folder is a record, but its settings file could not be read: {exc}",
            )
        return Plan(target=target, action=JOIN, profile=existing.sync_profile)

    if target.is_dir() and _has_contents(target):
        return refuse(
            target,
            f"{target} already has other things in it and is not a record. A record "
            f"needs a folder of its own: choose a new name, or an empty folder.",
        )

    if not base.is_dir():
        return refuse(target, f"There is no folder at {base}.")
    if not os.access(base, os.W_OK | os.X_OK):
        return refuse(target, f"This app is not allowed to make a folder inside {base}.")

    return Plan(
        target=target,
        action=CREATE,
        profile=profile,
        config_text=config_mod.NEW_VAULT_CONFIG.format(
            profile=profile.value, port=config_mod.DEFAULT_PORT, locale=config_mod.DEFAULT_LOCALE
        ),
    )


def carry_out(plan: Plan) -> vault_mod.Vault:
    """Create or join, record where the folder is, and make sure this computer has an identity."""
    if not plan.ok or plan.action not in (CREATE, JOIN):
        raise ConfigError(plan.refusal or "nothing to do")
    target = plan.target
    if plan.action == CREATE:
        target.mkdir(parents=False, exist_ok=True)
        vault_mod.scaffold(target)
        config_mod.write_new_vault_config(target / CONFIG_FILENAME, plan.profile)
    vault_mod.write_pointer(target)
    ensure_identity()
    firstrun.mark()
    return vault_mod.Vault.open(target)


def ensure_identity() -> device_mod.DeviceIdentity | None:
    """Issue this computer's identity if it has none, as ``check --fix`` does.

    A missing identity is a new computer, and issuing one asks nothing of
    anybody. An identity issued on *another* computer is left alone and
    reported: that is a copied or restored file, and replacing it is the
    person's decision (:func:`replace_identity`).
    """
    if os.environ.get(device_mod.ENV_DEVICE):
        return None
    if not device_mod.identity_path().exists():
        device_mod.issue()
    try:
        return device_mod.load()
    except HealthAgentError:
        return None


def replace_identity() -> device_mod.DeviceIdentity:
    """Give this computer a new identity, keeping the old file beside it.

    The old file is renamed, not deleted: it names the event files that computer
    wrote, and those stay in the record under that name.
    """
    current = device_mod.identity_path()
    if current.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        current.rename(current.with_name(f"{current.name}.replaced-{stamp}"))
    return device_mod.issue()


def _has_contents(folder: Path) -> bool:
    try:
        return any(child.name not in IGNORABLE for child in folder.iterdir())
    except OSError:
        return True
