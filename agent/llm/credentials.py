"""Getting the API key, from anywhere except the vault.

``config.toml`` sits at the vault root and syncs to Dropbox, Drive or Nextcloud.
A key written there has been uploaded to a third party by definition, so the
config holds only a *reference* to where the key lives. The config loader already
refuses a live credential; this module is the other half — the places a key may
actually come from.

Resolution order, first hit wins:

1. the environment variable named by ``api_key_env``
2. the OS keychain, service ``health-agent``, account ``vlm-endpoint``
3. ``~/.config/health-agent/credentials``, mode ``0600``, outside the vault

**First source that is *present* wins, and a present-but-empty source is a hard
failure.** Falling through from an empty environment variable to the keychain
would silently use a key the user thought they had overridden; sending the empty
value would put a blank ``Authorization: Bearer`` header on the wire and come
back as a 401 that looks like a rotated key. Both hide a one-line
misconfiguration behind a confusing symptom, so an empty value says which source
produced it and stops.

Read **per call**, never cached at boot. A rotated key should cost one failed job
rather than a restart and a puzzled half hour.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .. import device as device_mod
from ..errors import CredentialError

KEYRING_SERVICE = "health-agent"
KEYRING_ACCOUNT = "vlm-endpoint"

CREDENTIALS_FILENAME = "credentials"
#: Anything wider is refused. Not a theoretical concern: a world-readable key on
#: a shared machine is exactly the disclosure the whole design is avoiding.
MAX_FILE_MODE = 0o600

SOURCE_ENV = "environment"
SOURCE_KEYCHAIN = "keychain"
SOURCE_FILE = "credentials file"


def credentials_path() -> Path:
    """``~/.config/health-agent/credentials``. Beside the device identity."""
    return device_mod.config_home() / CREDENTIALS_FILENAME


@dataclass(frozen=True)
class Credential:
    """A resolved key and where it came from.

    ``value`` never appears in a repr, a log line, an error message or any API
    response. :mod:`agent.llm.redaction` scrubs it from anything that escapes.
    """

    value: str
    source: str

    def __repr__(self) -> str:  # pragma: no cover - trivial, but load-bearing
        return f"Credential(source={self.source!r}, value=<redacted>)"


def _from_env(name: str | None) -> Credential | None:
    if not name:
        return None
    raw = os.environ.get(name)
    if raw is None:
        return None
    if not raw.strip():
        raise CredentialError(
            f"the environment variable {name} is set but empty. An empty key is a "
            f"misconfiguration, not a credential: sending it would put a blank "
            f"Authorization header on the wire and come back as a 401 that looks "
            f"like a rotated key. Unset {name} to fall through to the keychain, or "
            f"set it to the real key."
        )
    return Credential(raw.strip(), f"{SOURCE_ENV} ({name})")


def _from_keychain() -> Credential | None:
    try:
        import keyring  # noqa: PLC0415 - optional dependency, imported where used
    except ImportError:
        return None
    try:
        raw = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception as exc:  # keyring raises backend-specific errors
        raise CredentialError(
            f"the OS keychain could not be read: {exc}. On a headless machine there "
            f"may be no Secret Service running — set the key in an environment "
            f"variable instead and name it in config.toml under "
            f"[models.vlm.auth] api_key_env."
        ) from None
    if raw is None:
        return None
    if not raw.strip():
        raise CredentialError(
            f"the keychain entry {KEYRING_SERVICE}/{KEYRING_ACCOUNT} exists but is "
            f"empty. Set it to the real key or delete the entry; an empty value is "
            f"never sent."
        )
    return Credential(raw.strip(), SOURCE_KEYCHAIN)


def _from_file(path: Path, vault_root: Path | None) -> Credential | None:
    if not path.exists():
        return None
    resolved = path.resolve()
    if vault_root is not None and _is_inside(resolved, vault_root.resolve()):
        raise CredentialError(
            f"the credentials file {resolved} is inside the vault at {vault_root}. "
            f"The vault syncs to third-party storage, so a key there is already "
            f"disclosed: move it to {credentials_path()} and rotate the key."
        )
    mode = stat.S_IMODE(resolved.stat().st_mode)
    if mode & ~MAX_FILE_MODE:
        raise CredentialError(
            f"the credentials file {resolved} has mode {mode:04o}, which is wider "
            f"than {MAX_FILE_MODE:04o}. Refusing to read it — run "
            f"`chmod 600 {resolved}`, and treat the key as disclosed if the machine "
            f"is shared."
        )
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise CredentialError(f"cannot read {resolved}: {exc}") from None
    value = _first_value(raw)
    if value is None:
        raise CredentialError(
            f"the credentials file {resolved} exists but holds no key. Write the key "
            f"on its own line, or delete the file to fall through to the keychain."
        )
    return Credential(value, SOURCE_FILE)


def _first_value(text: str) -> str | None:
    """The first non-comment, non-blank line, with an optional ``key=`` prefix."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            _, _, after = stripped.partition("=")
            stripped = after.strip().strip('"').strip("'")
        if stripped:
            return stripped
    return None


def _is_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolve(
    api_key_env: str | None = None,
    vault_root: Path | None = None,
    path: Path | None = None,
) -> Credential:
    """The key, or a :class:`CredentialError` saying exactly what to do.

    Fails closed. There is no path through this function that returns an empty
    string, and no caller may treat "no credential" as "send no header": the
    endpoint is authenticated because every device on a tailnet should not be
    able to query a health record's inference server.
    """
    for source in (
        lambda: _from_env(api_key_env),
        _from_keychain,
        lambda: _from_file(path or credentials_path(), vault_root),
    ):
        found = source()
        if found is not None:
            return found

    named = f" {api_key_env}," if api_key_env else ""
    raise CredentialError(
        f"no credential found for the inference endpoint. Looked in:{named} the "
        f"environment; the OS keychain under {KEYRING_SERVICE}/{KEYRING_ACCOUNT}; and "
        f"{credentials_path()}.\n\n"
        f"Set one with `health-agent set-key`, which writes to the keychain. It "
        f"cannot go in config.toml — that file syncs with the vault."
    )


def status(
    api_key_env: str | None = None,
    vault_root: Path | None = None,
    path: Path | None = None,
) -> tuple[str, str | None]:
    """``("ok", "keychain")``, ``("missing", None)`` or ``("error", reason)``.

    What ``/api/health`` is allowed to report in phase 5, and what ``probe``
    prints now. Deliberately not the key, not a prefix of the key, and not its
    length — see MODELS.md, "The browser never sees the key".
    """
    try:
        found = resolve(api_key_env, vault_root, path)
    except CredentialError as exc:
        message = str(exc)
        return ("missing", None) if message.startswith("no credential") else ("error", message)
    return ("ok", found.source)
