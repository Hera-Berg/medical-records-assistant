"""Device identity.

The identity lives at ``~/.config/health-agent/device``, deliberately **outside
the vault**. It cannot come from ``config.toml``: that file syncs to every
machine, so every machine would read the same device id, write to the same
monthly shard, and produce exactly the "conflicted copy" data loss that the
one-file-per-device rule exists to prevent.

Because it lives outside the vault, the identity also survives the vault folder
being moved into Dropbox or restored somewhere else — which is the normal life
of this folder, not an edge case.
"""

from __future__ import annotations

import json
import os
import platform as platform_mod
import re
import secrets
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .distribution import for_terminal, packaged
from .errors import DeviceIdentityError, DeviceIdentityMismatch

ENV_DEVICE = "HEALTH_DEVICE"
ENV_LABEL = "HEALTH_DEVICE_LABEL"
ENV_CONFIG_HOME = "HEALTH_AGENT_CONFIG_HOME"

DEVICE_ID_MAX_LENGTH = 24
_DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_SUFFIX_LENGTH = 4


def config_home() -> Path:
    """The out-of-vault config directory holding the identity and vault pointer."""
    override = os.environ.get(ENV_CONFIG_HOME)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "health-agent"


def identity_path() -> Path:
    return config_home() / "device"


def is_valid_device_id(value: object) -> bool:
    """True if *value* is usable as a device id, and therefore in a filename."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= DEVICE_ID_MAX_LENGTH
        and bool(_DEVICE_ID_RE.match(value))
        and not value.endswith("-")
    )


def slugify(value: str) -> str:
    """Reduce *value* to the device id charset ``[a-z0-9-]``."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "device"


def normalise_hostname(value: str) -> str:
    """Normalise a hostname for comparison.

    macOS flaps between ``foo`` and ``foo.local`` depending on how the name is
    queried, and a trailing dot is a legal FQDN. Stripping those is a fixed
    rule, not a heuristic: the comparison stays exact afterwards.
    """
    name = value.strip().rstrip(".").lower()
    for suffix in (".local", ".lan", ".home", ".localdomain"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def current_hostname() -> str:
    return normalise_hostname(socket.gethostname())


def current_platform() -> str:
    return platform_mod.system()


@dataclass(frozen=True)
class DeviceIdentity:
    """A device's identity, and where it came from."""

    id: str
    label: str
    created: str
    hostname: str
    platform: str
    source: str  # "env" | "file"
    path: Path | None = None

    @property
    def machine_matches(self) -> bool:
        """False if this identity was issued on a different machine.

        An identity issued elsewhere means the file was cloned or restored from
        a backup. Two machines sharing a device id share a shard, so appends are
        refused until it is re-issued.
        """
        if self.source == "env":
            return True
        return (
            self.hostname == current_hostname()
            and self.platform == current_platform()
        )

    def mismatch_reason(self) -> str | None:
        if self.machine_matches:
            return None
        return (
            f"device identity {self.id!r} was issued on "
            f"{self.hostname!r}/{self.platform!r} but this machine is "
            f"{current_hostname()!r}/{current_platform()!r}. The identity file was "
            f"cloned or restored from a backup. Appending under a device id another "
            f"machine may also be using is how shards collide and events are lost. "
            f"Give this computer a new identity"
            + (
                " — the app offers to when it starts."
                if packaged()
                else f" (delete {self.path} and run `health-agent check --fix`) or "
                f"set {ENV_DEVICE} to a distinct id for this machine."
            )
        )

    def require_appendable(self) -> None:
        """Raise if this identity must not be used for appends."""
        reason = self.mismatch_reason()
        if reason is not None:
            raise DeviceIdentityMismatch(reason)


def _new_id(label_slug: str) -> str:
    suffix = "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(_SUFFIX_LENGTH))
    room = DEVICE_ID_MAX_LENGTH - _SUFFIX_LENGTH - 1
    stem = label_slug[:room].rstrip("-") or "device"
    return f"{stem}-{suffix}"


def issue(label: str | None = None, path: Path | None = None) -> DeviceIdentity:
    """Create the identity file for this machine. Never overwrites an existing one."""
    target = path or identity_path()
    label = label or os.environ.get(ENV_LABEL) or current_hostname()
    # The hostname ends up in filenames inside a folder that may later sync to a
    # third party, which is why the label is overridable.
    identity = {
        "id": _new_id(slugify(label)),
        "label": label,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hostname": current_hostname(),
        "platform": current_platform(),
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass

    body = json.dumps(identity, indent=2, sort_keys=True) + "\n"
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise DeviceIdentityError(
            f"a device identity already exists at {target}; delete it deliberately "
            f"before re-issuing, since events already written under the old id "
            f"remain in the log"
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    return _from_mapping(identity, source="file", path=target)


def _from_mapping(data: dict, source: str, path: Path | None) -> DeviceIdentity:
    missing = [k for k in ("id", "label", "created", "hostname", "platform") if k not in data]
    if missing:
        raise DeviceIdentityError(
            f"device identity at {path} is missing {', '.join(missing)}"
            + (
                "; the app offers to issue a new one when it starts"
                if packaged()
                else "; delete it and run `health-agent check --fix` to re-issue"
            )
        )
    if not is_valid_device_id(data["id"]):
        raise DeviceIdentityError(
            f"device id {data['id']!r} is not usable in a filename: it must match "
            f"[a-z0-9-] and be at most {DEVICE_ID_MAX_LENGTH} characters"
        )
    return DeviceIdentity(
        id=data["id"],
        label=data["label"],
        created=data["created"],
        hostname=normalise_hostname(data["hostname"]),
        platform=data["platform"],
        source=source,
        path=path,
    )


def load(path: Path | None = None) -> DeviceIdentity:
    """Resolve this machine's identity.

    ``HEALTH_DEVICE`` wins outright and bypasses the file, including its
    clone detection — it is the documented escape hatch for a restored machine
    and for headless runs.
    """
    override = os.environ.get(ENV_DEVICE)
    if override:
        if not is_valid_device_id(override):
            raise DeviceIdentityError(
                f"{ENV_DEVICE}={override!r} is not a usable device id: it must match "
                f"[a-z0-9-] and be at most {DEVICE_ID_MAX_LENGTH} characters"
            )
        return DeviceIdentity(
            id=override,
            label=override,
            created="",
            hostname=current_hostname(),
            platform=current_platform(),
            source="env",
            path=None,
        )

    target = path or identity_path()
    if not target.exists():
        raise DeviceIdentityError(
            f"no device identity at {target}."
            + (
                " The app gives this computer one when it starts; quit it and open it again."
                if packaged()
                else f" Run `health-agent check --fix` to issue one, or set {ENV_DEVICE}."
            )
            + " It lives outside the vault on purpose: an id shared through the "
            "synced config would put two machines on one shard."
        )
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeviceIdentityError(f"cannot read device identity at {target}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeviceIdentityError(
            f"device identity at {target} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise DeviceIdentityError(f"device identity at {target} must be a JSON object")
    return _from_mapping(data, source="file", path=target)
