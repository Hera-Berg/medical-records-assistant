"""Where a new record could live, found on this computer without asking anyone.

The first option is always **a folder on this computer**, preselected and named
for someone who has never heard of sync. Accepting every default on the first
run has to end in a working record, and the one location that needs no account,
no client and no other machine is the local one. Folders kept by Dropbox, Google
Drive and Nextcloud follow, for the people who recognise those names.

Each is found by reading the **sync client's own files on this disk** — Dropbox's
``info.json``, Nextcloud's ``nextcloud.cfg``, the folders Google Drive for
desktop mounts. No service is contacted and no API is called: the record never
talks to those services, and finding where they keep a folder is no exception.
This is also not a storage backend. Each location is a path and the
``sync_profile`` that path implies, and nothing more.
"""

from __future__ import annotations

import json
import os
import re
import string
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..config import SyncProfile
from ..runtime import platforms

#: What the new folder is called unless the person changes it. Plain words, so
#: it reads as what it is in a file manager with nothing installed.
DEFAULT_FOLDER_NAME = "Health record"

LOCAL = "this-computer"
OTHER = "somewhere-else"


@dataclass(frozen=True)
class Location:
    """A place the record folder could be created inside."""

    key: str
    label: str
    #: The folder the record goes *inside*.
    parent: Path
    profile: SyncProfile
    #: One sentence on what choosing it means, in the patient's words.
    explanation: str
    recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "parent": str(self.parent),
            "profile": self.profile.value,
            "profile_label": self.profile.label,
            "explanation": self.explanation,
            "recommended": self.recommended,
            "warning": self.profile.setup_warning,
        }


def detect(
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    drives: tuple[str, ...] | None = None,
) -> list[Location]:
    """Every location worth offering, the local one first. Never raises."""
    home = home or Path.home()
    env = os.environ if env is None else env
    platform = platform or sys.platform

    found: list[Location] = [_local(home)]
    seen: set[str] = {_key(found[0].parent)}
    for location in (
        *_dropbox(home, env, platform),
        *_google_drive(home, platform, drives),
        *_nextcloud(home, env, platform),
    ):
        if _key(location.parent) not in seen and location.parent.is_dir():
            seen.add(_key(location.parent))
            found.append(location)
    return found


def profile_for(path: Path, locations: list[Location]) -> SyncProfile:
    """The profile a chosen folder implies: the synced location it sits inside, if any."""
    for location in locations:
        if location.profile is not SyncProfile.LOCAL and platforms.is_inside(path, location.parent):
            return location.profile
    return SyncProfile.LOCAL


def _key(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _local(home: Path) -> Location:
    documents = home / "Documents"
    return Location(
        key=LOCAL,
        label="A folder on this computer (recommended)",
        parent=documents if documents.is_dir() else home,
        profile=SyncProfile.LOCAL,
        explanation=(
            "Kept only on this computer, in your Documents folder. Nothing else is "
            "needed, and you can move it somewhere synced later."
        ),
        recommended=True,
    )


# --- Dropbox ---------------------------------------------------------------


def _dropbox(home: Path, env: Mapping[str, str], platform: str) -> list[Location]:
    candidates = [home / ".dropbox" / "info.json"]
    if platform == "win32":
        candidates = [
            Path(env[name]) / "Dropbox" / "info.json"
            for name in ("APPDATA", "LOCALAPPDATA")
            if env.get(name)
        ]
    found: list[Location] = []
    for info in candidates:
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for account in ("personal", "business"):
            entry = data.get(account)
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                found.append(_dropbox_location(Path(entry["path"]), account))
    if platform == "darwin":
        # Newer Dropbox on macOS lives under File Provider and may not write the
        # old info.json at all.
        for folder in sorted(_children(home / "Library" / "CloudStorage", "Dropbox")):
            found.append(_dropbox_location(folder, "personal"))
    return found


def _dropbox_location(path: Path, account: str) -> Location:
    return Location(
        key=f"dropbox-{account}",
        label="Your Dropbox folder" if account == "personal" else "Your Dropbox folder (work)",
        parent=path,
        profile=SyncProfile.DROPBOX,
        explanation="Copied to your Dropbox account and to your other computers that use it.",
    )


# --- Google Drive ----------------------------------------------------------


def _google_drive(home: Path, platform: str, drives: tuple[str, ...] | None) -> list[Location]:
    folders: list[Path] = []
    if platform == "darwin":
        for account in sorted(_children(home / "Library" / "CloudStorage", "GoogleDrive-")):
            folders.append(account / "My Drive")
    elif platform == "win32":
        # Google Drive for desktop mounts a drive letter, G: unless that was
        # taken. The letters are only looked at, never listed recursively.
        letters = drives if drives is not None else tuple(string.ascii_uppercase[3:])
        for letter in letters:
            folders.append(Path(f"{letter}:\\") / "My Drive")
    return [
        Location(
            key="gdrive" if index == 0 else f"gdrive-{index + 1}",
            label="Your Google Drive" if index == 0 else f"Your Google Drive ({folder.parent.name})",
            parent=folder,
            profile=SyncProfile.GDRIVE,
            explanation="Copied to your Google account and to your other computers that use it.",
        )
        for index, folder in enumerate(f for f in folders if f.is_dir())
    ]


# --- Nextcloud -------------------------------------------------------------

_NEXTCLOUD_PATH = re.compile(r"^\d+\\Folders\\\d+\\localPath=(?P<path>.+)$")


def _nextcloud(home: Path, env: Mapping[str, str], platform: str) -> list[Location]:
    if platform == "darwin":
        config = home / "Library" / "Preferences" / "Nextcloud" / "nextcloud.cfg"
    elif platform == "win32":
        config = Path(env.get("APPDATA", str(home / "AppData" / "Roaming"))) / "Nextcloud" / "nextcloud.cfg"
    else:
        base = env.get("XDG_CONFIG_HOME") or str(home / ".config")
        config = Path(base) / "Nextcloud" / "nextcloud.cfg"
    folders: list[Path] = []
    try:
        for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
            match = _NEXTCLOUD_PATH.match(line.strip())
            if match:
                folders.append(Path(match.group("path").strip().rstrip("/\\") or "/"))
    except OSError:
        pass
    if not folders and (home / "Nextcloud").is_dir():
        folders.append(home / "Nextcloud")
    return [
        Location(
            key="nextcloud" if index == 0 else f"nextcloud-{index + 1}",
            label="Your Nextcloud folder",
            parent=folder,
            profile=SyncProfile.NEXTCLOUD,
            explanation="Copied to your Nextcloud server and to your other computers that use it.",
        )
        for index, folder in enumerate(folders)
    ]


def _children(directory: Path, prefix: str) -> list[Path]:
    try:
        return [child for child in directory.iterdir() if child.name.startswith(prefix) and child.is_dir()]
    except OSError:
        return []
