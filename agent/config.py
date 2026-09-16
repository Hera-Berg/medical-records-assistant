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

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

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
    def label(self) -> str:
        """The option as a person would say it, not as the file spells it."""
        return {
            SyncProfile.LOCAL: "A folder on this computer",
            SyncProfile.DROPBOX: "Synced by Dropbox",
            SyncProfile.GDRIVE: "Synced by Google Drive",
            SyncProfile.NEXTCLOUD: "Synced by Nextcloud",
            SyncProfile.OTHER: "Synced by another client",
        }[self]

    @property
    def effects(self) -> tuple[str, ...]:
        """What choosing this actually changes, in sentences.

        Written out rather than asserted, because the honest summary of this
        setting is that it changes very little: an interface that says "this
        matters" without saying how invites the reader to assume it moves files.
        """
        forks = {
            SyncProfile.DROPBOX: "files named \u201cconflicted copy\u201d",
            SyncProfile.GDRIVE: "files named \u201c(1)\u201d inside events/",
            SyncProfile.NEXTCLOUD: "files named \u201c_conflict-\u2026\u201d and \u201cconflicted copy\u201d",
        }.get(self, "every kind of forked file this app knows about")
        effects = [f"Watches for {forks} and asks you to merge them."]
        if self.verify_readback:
            effects.append(
                "Reads back each new entry in the log after writing it, because a "
                "virtual drive can report a write as finished before it is."
            )
        else:
            effects.append("Trusts the filesystem that a write has landed.")
        if self.may_have_placeholders:
            effects.append(
                "Expects files that are listed but not downloaded, and refuses to "
                "write to one rather than filling it in with the wrong contents."
            )
        return tuple(effects)

    @property
    def setup_warning(self) -> str | None:
        if self is SyncProfile.LOCAL:
            return None
        service = {
            SyncProfile.DROPBOX: "Dropbox",
            SyncProfile.GDRIVE: "Google Drive",
            SyncProfile.NEXTCLOUD: "Nextcloud",
        }.get(self, "your sync client")
        return (
            f"Your whole record is in {service}. Anyone who can get into that "
            f"account can read all of it \u2014 every document, every note, the "
            f"lot \u2014 and a shared folder link cannot reliably be taken back "
            f"once it is out. Never put a password or key in config.toml: that "
            f"file is inside the folder, so it syncs too. To show the record to "
            f"someone, send them a summary you have generated, not the folder."
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


# --- writing the one key this program is allowed to write ------------------
#
# ``check --fix`` never writes ``config.toml``, on the grounds that a config
# this program invented is a config nobody has read. That rule is about the app
# *inventing* settings. This is the opposite: the owner of the record choosing
# one value deliberately, in an interface that tells them exactly what it means
# before they choose it. So exactly one key may be written, and it is written by
# rewriting one line rather than by re-serialising the file.
#
# Re-serialising would be the obvious implementation and it would be wrong. This
# file is hand-written and hand-read: it carries comments explaining what a key
# is for, the endpoint URL with the note that it must resolve to 100.x, and the
# warning about never putting a key in it. A round-trip through ``tomllib`` and
# a writer would silently delete every one of those, and the person who opens
# the file in five years would find a machine's version of their own config.

#: The assignment, as it appears in a file. The value is a TOML basic or literal
#: string; anything else never got past :func:`load`.
_SYNC_PROFILE_LINE = re.compile(
    r"^(?P<lead>\s*sync_profile\s*=\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*')"
    r"(?P<rest>\s*(?:#.*)?)$"
)
_ANY_SYNC_PROFILE = re.compile(r"^\s*sync_profile\s*=")
_TABLE_HEADER = re.compile(r"^\s*\[")

#: Every conflict pattern, regardless of the configured profile. A profile that
#: is wrong is one of the reasons a fork exists, so narrowing the patterns by it
#: would disable the guard exactly when it is needed — the same reasoning that
#: makes the shard filename grammar, not the profile, decide what the merged
#: event view contains.
_ALL_CONFLICT_PATTERNS = (_DROPBOX_CONFLICT, _NEXTCLOUD_CONFLICT, _NUMBERED_COPY)


def conflict_forks(path: Path) -> list[Path]:
    """Sync-client forks of *path* sitting beside it.

    Dropbox writes ``config (Elwood's conflicted copy 2026-09-08).toml``, Google
    Drive writes ``config (1).toml``, Nextcloud writes
    ``config_conflict-20260908-141500.toml``. All three mean the same thing: two
    machines have disagreed about this file and the client has given up merging.
    """
    stem = path.name.split(".")[0].lower()
    found: list[Path] = []
    try:
        siblings = sorted(path.parent.iterdir())
    except OSError:
        return []
    for sibling in siblings:
        if sibling.name == path.name:
            continue
        if not sibling.name.lower().startswith(stem):
            continue
        try:
            if not sibling.is_file():
                continue
        except OSError:
            continue
        if any(pattern.search(sibling.name) for pattern in _ALL_CONFLICT_PATTERNS):
            found.append(sibling)
    return found


def _rewrite_sync_profile(text: str, profile: SyncProfile) -> str:
    """Return *text* with ``sync_profile`` set to *profile*, changing nothing else.

    Only the top-level assignment is touched — anything after the first table
    header belongs to that table, and a ``sync_profile`` key inside one is a
    different key with the same name.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)

    limit = len(lines)
    for index, line in enumerate(lines):
        if _TABLE_HEADER.match(line):
            limit = index
            break

    assignment = f'sync_profile = "{profile.value}"'

    for index in range(limit):
        if not _ANY_SYNC_PROFILE.match(lines[index]):
            continue
        match = _SYNC_PROFILE_LINE.match(lines[index])
        if match:
            # The trailing comment is the one explaining what the options are.
            # It is the reason this is a line rewrite rather than a file rewrite.
            lines[index] = (
                f'{match.group("lead")}"{profile.value}"{match.group("rest")}'
            )
        else:
            lines[index] = assignment
        return newline.join(lines)

    # Absent. Put it at the end of the top-level keys, before the first table,
    # rather than at the very top where it would sit above the file's own
    # explanatory header comment.
    insert_at = limit
    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, assignment)
    if insert_at + 1 < len(lines) and _TABLE_HEADER.match(lines[insert_at + 1]):
        lines.insert(insert_at + 1, "")
    return newline.join(lines)


def _guarded_write(
    path: Path,
    rewrite: Callable[[str], str],
    verify: Callable[[Config], str | None],
) -> Config:
    """Rewrite *path* through *rewrite*, or leave it exactly as it was.

    Four guards, in order, because each one protects against a different way of
    losing the file:

    1. **A sync fork beside it is a refusal.** Writing into a file the sync
       client is actively forking is how the whole config goes missing: two
       writers already disagree about its contents, and a third write means the
       client resolves that disagreement against bytes nobody chose.
    2. **The write is atomic.** A temporary file in the same directory, then
       ``os.replace``. A crash mid-write leaves the old file intact rather than
       a truncated one the server will not start from.
    3. **The result is re-read and validated, and restored if it fails.** The
       rewrite is a regex over a hand-edited file; if it produced something
       ``load`` rejects, the original bytes go back and the caller is told.
    4. **The reload has to say what was asked for.** ``verify`` re-reads the
       value out of the loaded config and returns a sentence if it disagrees.
       A file that loads but does not mean what was written is the failure a
       regex over hand-edited TOML actually has — a second ``[models.vlm]``
       table further down, an inline table, a key that was already there twice
       — and it is silent unless something reads the value back.
    """
    if not path.exists():
        raise ConfigError(
            f"no {CONFIG_FILENAME} at {path} to change. This program does not "
            f"invent one: create it first, with the template from `health-agent "
            f"check`."
        )

    forks = conflict_forks(path)
    if forks:
        names = ", ".join(sorted(fork.name for fork in forks))
        raise ConfigError(
            f"{path.name} was not changed: a sync client has forked it and left "
            f"{names} beside it. Two machines have already disagreed about this "
            f"file, and writing a third version into it is how the whole "
            f"configuration goes missing. Merge the fork by hand, delete it, and "
            f"try again."
        )

    try:
        original = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path} is not valid UTF-8: {exc}") from exc

    updated = rewrite(text)
    if updated == text:
        # Already says this. Nothing is written — an unchanged file keeps its
        # mtime, which is one less thing for the sync client to think about.
        return load(path)

    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(updated.encode("utf-8"))
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ConfigError(f"cannot write {path}: {exc}") from exc

    try:
        config = load(path)
    except ConfigError:
        path.write_bytes(original)
        raise
    disagreement = verify(config)
    if disagreement is not None:
        path.write_bytes(original)
        raise ConfigError(f"{path} was left unchanged: {disagreement}")
    return config


def set_sync_profile(path: Path, profile: SyncProfile) -> Config:
    """Write ``sync_profile`` into an existing ``config.toml``. Returns the reload."""

    def verify(config: Config) -> str | None:
        if config.sync_profile is profile:
            return None
        return (
            f"writing sync_profile = {profile.value!r} produced a file that reads "
            f"back as {config.sync_profile.value!r}. Set it by hand."
        )

    return _guarded_write(path, lambda text: _rewrite_sync_profile(text, profile), verify)


# --- the same write, for a key inside a table ------------------------------
#
# ``[models.vlm]`` is set from the settings screen for the same reason
# ``sync_profile`` is: the alternative is a person editing TOML by hand to point
# the record at their own box, and the thing they are most likely to get wrong
# while doing it — pasting a key in beside the URL — is the one mistake that
# cannot be taken back once the folder has synced.
#
# It is the same surgical rewrite, for the same reason. This file is hand-written
# and hand-read: it carries the comment saying the endpoint must resolve to
# 100.x, and the one saying never to put a key here. Re-serialising through a
# TOML writer would delete both, and the person who opens the file in five years
# would find a machine's version of their own config.

#: An assignment inside a table, captured so the value can be swapped without
#: touching the indentation or the trailing comment.
def _assignment(key: str) -> re.Pattern[str]:
    return re.compile(
        rf"^(?P<lead>\s*{re.escape(key)}\s*=\s*)"
        rf"(?P<value>\"[^\"]*\"|'[^']*'|[^#\s][^#]*?)"
        rf"(?P<rest>\s*(?:#.*)?)$"
    )


def _any_assignment(key: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(key)}\s*=")


def _header_of(line: str) -> tuple[str, ...] | None:
    """The table path a ``[a.b.c]`` line names, or ``None`` if it is not one.

    Quoted segments are read as literal names, which is what TOML means by
    them. Anything this cannot parse confidently returns a path that will not
    match — the write then appends a fresh table and the reload check catches
    it, rather than this guessing.
    """
    stripped = line.strip()
    if not stripped.startswith("[") or stripped.startswith("[["):
        return None
    end = stripped.find("]")
    if end == -1:
        return None
    inside = stripped[1:end].strip()
    if not inside:
        return None
    segments = []
    for raw in inside.split("."):
        name = raw.strip()
        if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'":
            name = name[1:-1]
        segments.append(name)
    return tuple(segments)


def _toml_string(value: str) -> str:
    """*value* as a TOML basic string.

    ``json.dumps`` with ``ensure_ascii=False``: TOML basic strings take the same
    escapes, and leaving non-ASCII literal avoids emitting the surrogate pairs
    JSON would and TOML forbids.
    """
    return json.dumps(value, ensure_ascii=False)


def _rewrite_values(text: str, values: Mapping[str, str]) -> str:
    """Return *text* with each dotted key in *values* set, changing nothing else.

    Keys are full dotted paths — ``models.vlm.base_url`` — and are grouped by
    the table they live in, because that is what a file is: a header line and
    the assignments under it. A key already present has its value swapped in
    place; a key missing from an existing table is appended to that table; a
    table that is not there at all is added at the end of the file.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)

    by_table: dict[tuple[str, ...], dict[str, str]] = {}
    for dotted, value in values.items():
        parts = tuple(dotted.split("."))
        by_table.setdefault(parts[:-1], {})[parts[-1]] = value

    for table in sorted(by_table, key=len):
        lines = _rewrite_table(lines, table, by_table[table])
    return newline.join(lines)


def _table_bounds(lines: list[str], table: tuple[str, ...]) -> tuple[int, int] | None:
    """Where *table*'s assignments start and end, or ``None`` if it is absent.

    The top-level table (an empty path) runs from the first line to the first
    header. Any other runs from just after its header to the next header.
    """
    if not table:
        for index, line in enumerate(lines):
            if _header_of(line) is not None:
                return (0, index)
        return (0, len(lines))
    for index, line in enumerate(lines):
        if _header_of(line) != table:
            continue
        end = len(lines)
        for after in range(index + 1, len(lines)):
            if _header_of(lines[after]) is not None:
                end = after
                break
        return (index + 1, end)
    return None


def _rewrite_table(
    lines: list[str], table: tuple[str, ...], values: Mapping[str, str]
) -> list[str]:
    bounds = _table_bounds(lines, table)
    if bounds is None:
        return _append_table(lines, table, values)

    start, end = bounds
    remaining = dict(values)
    for index in range(start, end):
        for key in list(remaining):
            if not _any_assignment(key).match(lines[index]):
                continue
            match = _assignment(key).match(lines[index])
            if match:
                # The trailing comment is the one explaining what the key is
                # for. It is the reason this is a line rewrite at all.
                lines[index] = (
                    f"{match.group('lead')}{_toml_string(remaining[key])}"
                    f"{match.group('rest')}"
                )
            else:
                lines[index] = f"{key} = {_toml_string(remaining[key])}"
            del remaining[key]
            break

    if not remaining:
        return lines

    # Appended at the end of the table's own lines, above whatever blank lines
    # separate it from the next header.
    insert_at = end
    while insert_at > start and not lines[insert_at - 1].strip():
        insert_at -= 1
    added = [f"{key} = {_toml_string(value)}" for key, value in remaining.items()]
    return lines[:insert_at] + added + lines[insert_at:]


def _append_table(
    lines: list[str], table: tuple[str, ...], values: Mapping[str, str]
) -> list[str]:
    """Add a table this file does not have, at the end, after one blank line."""
    if not table:  # pragma: no cover - the top level always exists
        return lines + [f"{key} = {_toml_string(v)}" for key, v in values.items()]
    tail = list(lines)
    while tail and not tail[-1].strip():
        tail.pop()
    header = "[" + ".".join(table) + "]"
    body = [f"{key} = {_toml_string(value)}" for key, value in values.items()]
    return tail + ["", header, *body, ""]


def _at(raw: Mapping[str, Any], dotted: str) -> Any:
    found: Any = raw
    for segment in dotted.split("."):
        if not isinstance(found, dict):
            return None
        found = found.get(segment)
    return found


def set_values(path: Path, values: Mapping[str, str]) -> Config:
    """Write dotted TOML keys into an existing ``config.toml``. Returns the reload.

    The same four guards as :func:`set_sync_profile`, and the same refusal to
    invent a file. The reload check reads every key back out of the parsed
    document: a rewrite that lands in the wrong table, or under a second header
    with the same name further down, loads perfectly and means something else.
    """

    def verify(config: Config) -> str | None:
        for dotted, wanted in values.items():
            found = _at(config.raw, dotted)
            if found != wanted:
                return (
                    f"writing {dotted} = {wanted!r} produced a file that reads back "
                    f"as {found!r}. The file may define that table more than once, "
                    f"or define it as an inline table; set the value by hand."
                )
        return None

    return _guarded_write(path, lambda text: _rewrite_values(text, values), verify)
