"""``config.toml`` at the vault root.

Two things make this file unusual. It contains **no vault path** — it is found
by the vault root, not the other way round — and it contains **no secrets ever**,
because it sits inside a folder that syncs to Dropbox, Drive or Nextcloud. A
credential written here has already been handed to a third party, so finding one
is a hard load failure.

``sync_profile`` is not a storage backend. Every profile is a folder on disk and
no sync client's API is ever called; the profile only selects the handful of
things that genuinely differ between them.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .errors import ConfigError, SecretInConfigError

CONFIG_FILENAME = "config.toml"

DEFAULT_PORT = 7777
DEFAULT_LOCALE = "en"

CONFIG_TEMPLATE = """\
# Health record vault configuration.
# This file lives inside the vault and syncs with it. Never put a key, token or
# password here — see MODELS.md, "Credentials".

sync_profile = "local"   # local | dropbox | gdrive | nextcloud | other
port = 7777
locale = "en"

[models.vlm]
base_url   = "https://macbook-pro.tailnet.ts.net/v1"   # must resolve to 100.x / RFC1918
model      = "Qwen3.8-Flash-Next-oQ4e-mtp"             # copy verbatim from /v1/models
ctx        = 16384
max_pixels = 1638400                      # 1280x1280
connect_timeout_s = 3
read_timeout_s    = 180
temperature       = 0.0
presence_penalty  = 0.0
thinking          = false

[models.vlm.auth]                         # a reference only — never the key itself
api_key_env = "HEALTH_VLM_TOKEN"
header      = "Authorization"
scheme      = "Bearer"

[models.asr]
name         = "faster-whisper-small"
compute_type = "int8"
"""

# Filename fragments that unambiguously mean "a sync client forked this file".
_DROPBOX_CONFLICT = re.compile(r"conflicted copy", re.IGNORECASE)
_NEXTCLOUD_CONFLICT = re.compile(r"_conflict-\d{8}-\d{6}", re.IGNORECASE)
# Google Drive for Desktop renames a fork to "name (1).ext". Generic enough to
# hit innocent filenames, so it is applied inside events/ only (see events.scan).
_NUMBERED_COPY = re.compile(r"\(\d+\)(\.[A-Za-z0-9]+)?$")


class SyncProfile(Enum):
    """Where the vault folder lives, and the three things that follow from it."""

    LOCAL = "local"
    DROPBOX = "dropbox"
    GDRIVE = "gdrive"
    NEXTCLOUD = "nextcloud"
    OTHER = "other"

    @property
    def conflict_patterns(self) -> tuple[re.Pattern[str], ...]:
        """Filename patterns the vault scan treats as a sync fork.

        ``local`` scans the full set rather than nothing: a local vault can
        still contain conflict files that arrived by restore, by copy from
        another machine, or from a period when the folder *was* synced. The
        narrowing exists to suppress false positives for the named clients.
        """
        if self is SyncProfile.DROPBOX:
            return (_DROPBOX_CONFLICT,)
        if self is SyncProfile.GDRIVE:
            return (_NUMBERED_COPY,)
        if self is SyncProfile.NEXTCLOUD:
            return (_NEXTCLOUD_CONFLICT, _DROPBOX_CONFLICT)
        return (_DROPBOX_CONFLICT, _NEXTCLOUD_CONFLICT, _NUMBERED_COPY)

    @property
    def verify_readback(self) -> bool:
        """Whether to re-read an appended line before reporting success.

        Virtual drives make weaker durability promises than a real filesystem:
        fsync can return on a file the client has not actually materialised.
        """
        return self is not SyncProfile.LOCAL

    @property
    def may_have_placeholders(self) -> bool:
        """Whether non-materialised (online-only) files are expected here."""
        return self in (SyncProfile.GDRIVE, SyncProfile.NEXTCLOUD, SyncProfile.OTHER)

    @property
    def setup_warning(self) -> str | None:
        if self is SyncProfile.LOCAL:
            return None
        return (
            f"vault is on {self.value}: anyone with access to that account can read "
            f"the whole record, and folder shares are effectively unrevocable. Never "
            f"put a credential in config.toml, and prefer a signed export over "
            f"sharing live storage."
        )


#: Top-level tables validated elsewhere. ``[models]`` belongs to the inference
#: layer, which parses it in ``agent.llm.settings``: this module owns the vault
#: and the secret scan, and knows nothing about any server's API. The scan below
#: still runs over it, which is what stops a key being written there.
_FUTURE_TABLES = frozenset({"models"})

_SECRET_NAME = re.compile(r"key|token|secret|password", re.IGNORECASE)
#: Names that match the pattern but only ever hold a *reference* to a secret.
_SECRET_NAME_ALLOWED = frozenset({"api_key_env", "header", "scheme"})


def scan_for_secrets(data: Any, path: str = "") -> list[str]:
    """Return dotted paths of keys that look like they hold a live credential.

    A matching key with a non-empty string value is a secret. So is a matching
    key holding a list or table — that is just a secret one level down.
    ``api_key_env``, ``header`` and ``scheme`` are references to where a key
    lives, never the key, and are allowed.
    """
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{path}.{key}" if path else str(key)
            if (
                isinstance(key, str)
                and _SECRET_NAME.search(key)
                and key not in _SECRET_NAME_ALLOWED
            ):
                if isinstance(value, str) and value.strip():
                    found.append(here)
                elif isinstance(value, (list, dict)) and value:
                    found.append(here)
            found.extend(scan_for_secrets(value, here))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            found.extend(scan_for_secrets(item, f"{path}[{i}]"))
    return found


@dataclass(frozen=True)
class Config:
    """A loaded, validated ``config.toml``."""

    path: Path
    sync_profile: SyncProfile = SyncProfile.LOCAL
    port: int = DEFAULT_PORT
    locale: str = DEFAULT_LOCALE
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def parse(data: dict[str, Any], path: Path) -> Config:
    """Validate an already-parsed TOML mapping."""
    leaked = scan_for_secrets(data)
    if leaked:
        raise SecretInConfigError(
            f"{path} contains what looks like a credential at "
            f"{', '.join(leaked)}. This file lives in the vault and syncs to third "
            f"party storage, so that value must be considered already disclosed: "
            f"rotate it, remove it from the file, and keep the key in the OS keychain "
            f"or an env var instead (see MODELS.md, 'Credentials')."
        )

    warnings: list[str] = []

    profile_name = data.get("sync_profile", SyncProfile.LOCAL.value)
    if not isinstance(profile_name, str):
        raise ConfigError(f"{path}: sync_profile must be a string")
    try:
        profile = SyncProfile(profile_name)
    except ValueError:
        raise ConfigError(
            f"{path}: unknown sync_profile {profile_name!r}; expected one of "
            f"{', '.join(p.value for p in SyncProfile)}"
        ) from None

    port = data.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigError(f"{path}: port must be an integer between 1 and 65535, got {port!r}")

    locale = data.get("locale", DEFAULT_LOCALE)
    if not isinstance(locale, str) or not locale.strip():
        raise ConfigError(f"{path}: locale must be a non-empty string")

    known = {"sync_profile", "port", "locale"} | _FUTURE_TABLES
    for key in sorted(data):
        if key not in known:
            warnings.append(
                f"{path}: unrecognised key {key!r} is ignored"
                + (
                    " — the vault root is resolved from --vault, HEALTH_VAULT or "
                    "~/.config/health-agent/vault, never from this file"
                    if key in ("vault_path", "vault", "vault_root")
                    else ""
                )
            )

    return Config(
        path=path,
        sync_profile=profile,
        port=port,
        locale=locale,
        raw=data,
        warnings=tuple(warnings),
    )


def load(path: Path) -> Config:
    """Read and validate ``config.toml`` at *path*."""
    if not path.exists():
        raise ConfigError(
            f"no {CONFIG_FILENAME} at {path}. Create it with:\n\n{CONFIG_TEMPLATE}"
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    return parse(data, path)
