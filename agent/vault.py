"""The vault: one folder the user nominates, holding everything.

The folder outlives the app. Assume it is opened in a file manager in five
years with nothing installed: markdown, JSON, JSONL and original files only.

The vault root cannot be recorded in ``config.toml``, because that file lives
*inside* the vault. It is resolved from, in order: an explicit ``--vault``, the
``HEALTH_VAULT`` environment variable, or a pointer file at
``~/.config/health-agent/vault``. The pointer sits beside the device identity,
outside the vault, so both survive the folder being moved into a sync client.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import device as device_mod
from .config import CONFIG_FILENAME, Config, SyncProfile
from .device import DeviceIdentity
from .distribution import for_terminal, packaged
from .errors import ConfigError, DeviceIdentityError, VaultError
from .events import log
from .events.envelope import Event
from .events.scan import ShardState, find_conflicts
from .ingest.store import RawStore, ingested_records

ENV_VAULT = "HEALTH_VAULT"

#: Created on scaffolding. Entity subdirectories under wiki/ are the projection
#: engine's business (phase 3), not this phase's.
VAULT_DIRS = ("raw", "events", "wiki", "exports", "bulk", ".agent", ".agent/logs")

#: Derived paths that are caches of *work*, not of the record, and so are left
#: out of the byte-identical rebuild comparison.
#:
#: The comparison exists to prove invariant 1 — delete everything derived,
#: replay from ``raw/`` and ``events/``, get the same bytes back. These three do
#: not weaken it, because none of them holds a fact:
#:
#: * ``.agent/index.sqlite`` is a lookup cache over the projection. Every
#:   question it answers is also answered by walking the projection, and there
#:   is a test that deletes it between two requests and compares the responses.
#:   Its bytes are not reproducible anyway: SQLite carries page-level state that
#:   differs between two runs with identical logical content.
#: * ``.agent/jobs.jsonl`` is the extraction queue. It records what has been
#:   *asked for*, not what is true; deleting it loses the queue, not the record,
#:   and the next run re-queues every artefact the log has no extraction for.
#: * ``.agent/logs`` is diagnostic output about the running process.
#:
#: A path earns its place here by being genuinely disposable, never by being
#: inconvenient to reproduce — the second would be a hole in the invariant
#: rather than an exception to the comparison. ``tests`` asserts the difference
#: by deleting all of them and requiring the wiki bytes to match.
NON_DETERMINISTIC_DERIVED = (
    ".agent/index.sqlite",
    ".agent/jobs.jsonl",
    ".agent/logs",
)


def is_non_deterministic(rel: str) -> bool:
    """Whether a vault-relative path is one of the caches excluded above."""
    posix = str(rel).replace("\\", "/")
    return any(
        posix == excluded or posix.startswith(excluded + "/")
        for excluded in NON_DETERMINISTIC_DERIVED
    )


#: Written at the root of a vault seeded by ``health-agent demo``. The name is
#: here rather than in :mod:`agent.demo` because the code that most needs to ask
#: "is this invented data" is code that should not be importing the seeder — the
#: inference layer, explaining why a vault has no endpoint, is the first case.
DEMO_MARKER_FILENAME = "DEMO-DATA.md"


def pointer_path() -> Path:
    return device_mod.config_home() / "vault"


@dataclass(frozen=True)
class ResolvedRoot:
    path: Path
    source: str  # "flag" | "env" | "pointer"


def resolve_root(explicit: str | os.PathLike[str] | None = None) -> ResolvedRoot:
    """Locate the vault root. Does not require it to exist."""
    if explicit:
        return ResolvedRoot(Path(explicit).expanduser().resolve(), "flag")

    env = os.environ.get(ENV_VAULT)
    if env:
        return ResolvedRoot(Path(env).expanduser().resolve(), "env")

    pointer = pointer_path()
    if pointer.exists():
        try:
            text = pointer.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise VaultError(f"cannot read vault pointer at {pointer}: {exc}") from exc
        if text:
            return ResolvedRoot(Path(text).expanduser().resolve(), "pointer")

    raise VaultError(
        f"no vault location known. Pass --vault PATH, set {ENV_VAULT}, or write the "
        f"path into {pointer}. It cannot live in {CONFIG_FILENAME}: that file is "
        f"inside the vault."
    )


def write_pointer(root: Path) -> Path:
    """Record *root* as the default vault for this machine."""
    pointer = pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(pointer.parent, 0o700)
    except OSError:
        pass
    pointer.write_text(str(root) + "\n", encoding="utf-8")
    return pointer


def scaffold(root: Path) -> list[str]:
    """Create the vault layout. Idempotent. Returns what it had to create."""
    created: list[str] = []
    if not root.exists():
        root.mkdir(parents=True)
        created.append(".")
    for name in VAULT_DIRS:
        target = root / name
        if not target.exists():
            target.mkdir(parents=True)
            created.append(name)
    return created


def is_writable(path: Path, probe: bool = False) -> bool:
    """Whether the vault can be written to.

    Without *probe* this asks the OS and touches nothing: a plain ``check`` must
    leave no trace, and a probe file in a synced folder bounces through the sync
    client for everyone. With *probe* — only when we are already permitted to
    write — it actually creates and removes a file, which catches read-only
    mounts and ACLs that ``access`` reports optimistically.
    """
    if not probe:
        return os.access(path, os.W_OK | os.X_OK)
    marker = path / ".agent" / ".write-probe"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
        marker.unlink()
        return True
    except OSError:
        return False


class Vault:
    """A located vault, its config, and this machine's identity within it."""

    def __init__(self, root: Path, config: Config, identity: DeviceIdentity | None = None):
        self.root = root
        self.config = config
        self._identity = identity

    @classmethod
    def open(
        cls,
        explicit: str | os.PathLike[str] | None = None,
        identity: DeviceIdentity | None = None,
    ) -> Vault:
        resolved = resolve_root(explicit)
        if not resolved.path.is_dir():
            raise VaultError(
                f"vault root {resolved.path} does not exist (resolved from "
                f"{resolved.source})."
                + (
                    " If it has moved, choose where it is now when the app starts."
                    if packaged()
                    else " Create it, or run `health-agent check --fix`."
                )
            )
        config = config_mod.load(resolved.path / CONFIG_FILENAME)
        return cls(resolved.path, config, identity)

    @property
    def profile(self) -> SyncProfile:
        return self.config.sync_profile

    @property
    def is_demo(self) -> bool:
        """Whether this vault was seeded by ``health-agent demo``.

        Read from the marker file rather than remembered, because the question
        is asked of a vault someone opened, not of one this process created.
        """
        return (self.root / DEMO_MARKER_FILENAME).is_file()

    @property
    def events_dir(self) -> Path:
        return self.root / "events"

    @property
    def identity(self) -> DeviceIdentity:
        if self._identity is None:
            self._identity = device_mod.load()
        return self._identity

    def append(self, event: Event) -> Path:
        """Append an event authored by this machine.

        Refuses if the device identity was issued on another machine: two
        machines sharing a device id share a shard, which is the failure mode
        the per-device split exists to prevent.
        """
        identity = self.identity
        identity.require_appendable()
        if event.device != identity.id:
            raise DeviceIdentityError(
                f"event device {event.device!r} is not this machine's device id "
                f"{identity.id!r}; a device only ever appends to its own shard"
            )
        return log.append(self.events_dir, event, self.profile)

    def read(self) -> log.LogRead:
        """The merged, deterministically ordered event stream."""
        return log.read_all(self.events_dir, self.profile)

    def conflicts(self):
        return find_conflicts(self.root, self.profile)

    @property
    def raw(self) -> RawStore:
        """The ``raw/`` store: originals, never modified, never derived."""
        return RawStore(self.root, self.profile)


def diagnose(
    explicit: str | os.PathLike[str] | None = None,
    fix: bool = False,
    deep: bool = False,
) -> dict[str, Any]:
    """Assemble the full state of the vault for ``health-agent check``.

    Reports rather than raises: the point is to describe a broken vault, not to
    fall over on one. ``fix`` permits exactly two writes — creating the vault
    directories and issuing this machine's device identity. It never writes
    ``config.toml``, because a config this program invented is a config nobody
    has read. ``deep`` re-hashes every stored artefact, which is a full read of
    the raw store and so is asked for rather than assumed.
    """
    report: dict[str, Any] = {"problems": [], "notes": []}
    problems: list[str] = report["problems"]
    notes: list[str] = report["notes"]

    # --- location -----------------------------------------------------------
    try:
        resolved = resolve_root(explicit)
    except VaultError as exc:
        report["vault"] = {"status": "unresolved", "error": str(exc)}
        problems.append(str(exc))
        report["ok"] = False
        return report

    root = resolved.path
    vault_info: dict[str, Any] = {
        "root": str(root),
        "source": resolved.source,
        "exists": root.is_dir(),
        "created": [],
        "writable": None,
    }
    report["vault"] = vault_info

    if fix:
        try:
            vault_info["created"] = scaffold(root)
            vault_info["exists"] = True
        except OSError as exc:
            problems.append(f"cannot create vault layout at {root}: {exc}")
        if explicit and vault_info["exists"]:
            try:
                vault_info["pointer"] = str(write_pointer(root))
                notes.append(f"recorded {root} as this machine's default vault")
            except OSError as exc:
                problems.append(f"cannot write vault pointer: {exc}")

    if not vault_info["exists"]:
        problems.append(
            f"vault root {root} does not exist (resolved from {resolved.source})"
            + for_terminal("; run `health-agent check --fix` to create it")
        )
        report["ok"] = False
        return report

    missing = [name for name in VAULT_DIRS if not (root / name).is_dir()]
    if missing:
        vault_info["missing_dirs"] = missing
        problems.append(
            f"missing vault directories: {', '.join(missing)}"
            + for_terminal(" — run `health-agent check --fix`")
        )
    vault_info["writable"] = is_writable(root, probe=fix)
    if not vault_info["writable"]:
        problems.append(f"vault at {root} is not writable")

    # --- config -------------------------------------------------------------
    config_path = root / CONFIG_FILENAME
    config_info: dict[str, Any] = {"path": str(config_path)}
    report["config"] = config_info
    config: Config | None = None
    try:
        config = config_mod.load(config_path)
    except ConfigError as exc:
        config_info["status"] = "missing" if not config_path.exists() else "invalid"
        config_info["error"] = str(exc)
        config_info["template"] = config_mod.CONFIG_TEMPLATE
        problems.append(str(exc))
    else:
        config_info["status"] = "ok"
        config_info["sync_profile"] = config.sync_profile.value
        config_info["port"] = config.port
        config_info["locale"] = config.locale
        config_info["warnings"] = list(config.warnings)
        notes.extend(config.warnings)
        if config.sync_profile.setup_warning:
            notes.append(config.sync_profile.setup_warning)

    # Without a valid config, scan with the widest pattern set rather than none.
    profile = config.sync_profile if config else SyncProfile.OTHER

    # --- device identity ----------------------------------------------------
    device_info: dict[str, Any] = {"path": str(device_mod.identity_path())}
    report["device"] = device_info
    identity: DeviceIdentity | None = None
    try:
        identity = device_mod.load()
    except DeviceIdentityError as exc:
        if fix and not device_mod.identity_path().exists() and not os.environ.get(
            device_mod.ENV_DEVICE
        ):
            try:
                identity = device_mod.issue()
                device_info["issued"] = True
                notes.append(f"issued device identity {identity.id}")
            except (DeviceIdentityError, OSError) as issue_exc:
                device_info["status"] = "error"
                device_info["error"] = str(issue_exc)
                problems.append(str(issue_exc))
        else:
            device_info["status"] = "missing"
            device_info["error"] = str(exc)
            problems.append(str(exc))

    if identity is not None:
        device_info.update(
            {
                "id": identity.id,
                "label": identity.label,
                "source": identity.source,
                "hostname": identity.hostname,
                "platform": identity.platform,
            }
        )
        mismatch = identity.mismatch_reason()
        if mismatch:
            device_info["status"] = "mismatch"
            device_info["error"] = mismatch
            problems.append(mismatch)
        else:
            device_info["status"] = "ok"

    # --- sync forks ---------------------------------------------------------
    # Resolved before the log so that a file which is both a fork and an
    # unrecognised shard name is reported once, as the more informative of the two.
    conflicts = find_conflicts(root, profile)
    conflict_paths = {c.path for c in conflicts}
    report["conflicts"] = [str(c.path.relative_to(root)) for c in conflicts]

    # --- the log ------------------------------------------------------------
    read = log.read_all(root / "events", profile)
    log_info: dict[str, Any] = {
        "event_count": len(read.events),
        "first_ts": read.events[0].ts if read.events else None,
        "last_ts": read.events[-1].ts if read.events else None,
        "shards": [
            {
                "name": shard.path.name,
                "state": shard.state.value,
                "detail": shard.detail,
                "device": shard.device,
                "month": shard.month,
                "events": shard.event_count,
                "bytes": shard.byte_size,
                "malformed": len(shard.malformed),
            }
            for shard in read.shards
        ],
        "malformed": [m.describe() for m in read.malformed],
        "anomalies": list(read.anomalies),
        "foreign_files": [str(p.relative_to(root)) for p in read.foreign_files],
        "duplicate_ids": [
            {"id": event_id, "shards": [p.name for p in paths]}
            for event_id, paths in read.duplicate_ids
        ],
        "unavailable": [
            {"name": s.path.name, "state": s.state.value, "detail": s.detail}
            for s in read.unavailable_shards
        ],
    }
    report["log"] = log_info

    for line in read.malformed:
        problems.append(f"unreadable event line: {line.describe()}")
    for anomaly in read.anomalies:
        notes.append(anomaly)
    for path in read.foreign_files:
        if path in conflict_paths:
            continue  # already reported, with better advice, as a sync fork
        problems.append(
            f"{path.relative_to(root)} is in events/ but is not a valid shard filename "
            f"(expected YYYY-MM.device.jsonl); its events are not in the merged view"
        )
    for event_id, paths in read.duplicate_ids:
        problems.append(
            f"event id {event_id} appears in {len(paths)} shards "
            f"({', '.join(p.name for p in paths)}); a shard was probably copied"
        )
    for shard in read.unavailable_shards:
        message = f"{shard.path.name}: {shard.detail}"
        if shard.state is ShardState.PLACEHOLDER:
            notes.append(message + " — the merged view is incomplete until it downloads")
        else:
            problems.append(message)

    for conflict in conflicts:
        problems.append(conflict.describe(root))

    # --- raw store ----------------------------------------------------------
    records, duplicate_ingests = ingested_records(read.events)
    raw = RawStore(root, profile).verify(records, deep=deep, duplicates=duplicate_ingests)
    report["raw"] = {
        "artifacts": raw.artifacts,
        "bytes": raw.bytes,
        "deep": raw.deep,
        "orphans": list(raw.orphans),
        "missing": list(raw.missing),
        "sidecar_missing": list(raw.sidecar_missing),
        "sidecar_disagrees": list(raw.sidecar_disagrees),
        "sidecar_orphaned": list(raw.sidecar_orphaned),
        "hash_mismatch": list(raw.hash_mismatch),
        "foreign": list(raw.foreign),
        "partials": list(raw.partials),
        "unavailable": list(raw.unavailable),
        "duplicate_events": list(raw.duplicate_events),
    }

    for rel in raw.missing:
        problems.append(
            f"{rel} is recorded in the event log but is not on disk; anything citing "
            f"it resolves to nothing. Restore it from a backup or from the sync "
            f"client's trash, or re-ingest the same file to put it back."
        )
    for rel in raw.orphans:
        problems.append(
            f"{rel} is in raw/ but no artifact.ingested event records it; it is not "
            f"part of the record. Adding the same file again records it."
        )
    for rel in raw.sidecar_missing:
        problems.append(
            f"{rel} has no sidecar; the file is still readable but the folder no "
            f"longer describes it on its own"
        )
    for detail in raw.sidecar_disagrees:
        problems.append(f"sidecar disagrees with the artefact beside it: {detail}")
    for rel in raw.sidecar_orphaned:
        problems.append(f"{rel} describes an artefact that is not there")
    for detail in raw.hash_mismatch:
        problems.append(detail)
    for rel in raw.foreign:
        problems.append(
            f"{rel} is in raw/ but does not match the artefact filename grammar, so "
            f"nothing can cite it. Ingest it or move it out of raw/."
        )
    for detail in raw.duplicate_events:
        problems.append(detail)
    for rel in raw.partials:
        notes.append(
            f"{rel} is a leftover from an interrupted ingest and holds no recorded "
            f"artefact; it is safe to delete"
        )
    for detail in raw.unavailable:
        notes.append(f"{detail} — not readable until the sync client downloads it")

    report["ok"] = not problems
    return report
