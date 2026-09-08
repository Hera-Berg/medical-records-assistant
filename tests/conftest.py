"""Shared fixtures.

Every test runs against a throwaway out-of-vault config home, so no test can
read or write the developer's real device identity or vault pointer.
"""

from __future__ import annotations

import os

import pytest

from agent import config as config_mod
from agent import device as device_mod
from agent import vault as vault_mod
from agent.events import envelope
from agent.vault import Vault

MINIMAL_CONFIG = 'sync_profile = "local"\nport = 7777\nlocale = "en"\n'


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Point the out-of-vault config home at tmp and clear every override."""
    home = tmp_path / "config-home"
    monkeypatch.setenv(device_mod.ENV_CONFIG_HOME, str(home))
    for name in (device_mod.ENV_DEVICE, device_mod.ENV_LABEL, vault_mod.ENV_VAULT):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture(autouse=True)
def restore_writable(tmp_path):
    """Put write permission back on everything under ``tmp_path`` afterwards.

    Stored artefacts are mode ``0o400``. Windows maps that to the read-only
    attribute, which makes ``shutil.rmtree`` refuse, and pytest cleans up older
    ``tmp_path`` directories with exactly that call — so without this the suite
    passes locally on Linux and fails on Windows CI a few runs in, which is a
    miserable thing to debug. Restoring the mode costs nothing and keeps
    teardown boring everywhere.
    """
    yield
    for path in tmp_path.rglob("*"):
        try:
            if path.is_file():
                os.chmod(path, 0o600)
        except OSError:
            pass


#: Minimal but genuine format headers, for the sniffing and ingest paths.
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"1 0 obj\n" * 8
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00" + b"\x00" * 64
WEBM = b"\x1aE\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1fB\x82\x84webm" + b"\x00" * 64


@pytest.fixture
def identity():
    return device_mod.issue(label="test-machine")


@pytest.fixture
def vault_root(tmp_path):
    root = tmp_path / "health"
    vault_mod.scaffold(root)
    (root / config_mod.CONFIG_FILENAME).write_text(MINIMAL_CONFIG, encoding="utf-8")
    return root


@pytest.fixture
def vault(vault_root, identity):
    return Vault.open(vault_root, identity=identity)


def note(device: str, ts: str | None = None, **payload) -> envelope.Event:
    """A user-authored note event, the simplest valid event."""
    return envelope.new("note.recorded", device, payload=payload or {"text": "x"}, ts=ts)


def proposal(device: str, ts: str | None = None, **payload) -> envelope.Event:
    """An agent-authored event, which must carry provenance."""
    return envelope.new(
        "claim.proposed",
        device,
        payload=payload or {"subject": "med:perindopril"},
        provenance={"model": "qwen3.5:9b", "artifact": "a3f91c"},
        ts=ts,
    )


# --- phase 3: hand-authored claims ------------------------------------------
#
# Phase 3 has no model, so every claim in these tests is written by hand. That
# is the point: the projection is a pure function of the event stream, and the
# stream is the only input it gets.

DEFAULT_ARTIFACT = "a3f91c"


def claim(
    device: str,
    subject: str,
    predicate: str,
    value,
    tier: str = "prescriber-issued",
    ts: str | None = None,
    artifact: str = DEFAULT_ARTIFACT,
    occurred=None,
    dispense=None,
    consequence: str | None = None,
    confidence: float | None = None,
    **extra,
) -> envelope.Event:
    """A ``claim.proposed`` event, as the extraction phase would emit one."""
    payload = {
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "evidence_tier": tier,
        "occurred_at": occurred,
        "artifact_ts": None,
        "captured_ts": None,
        "ingested_ts": ts,
    }
    if dispense is not None:
        payload["dispense"] = dispense
    if consequence is not None:
        payload["consequence"] = consequence
    if confidence is not None:
        payload["confidence"] = confidence
    payload.update(extra)
    return envelope.new(
        "claim.proposed",
        device,
        payload=payload,
        provenance={
            "model": "qwen3.5:9b",
            "model_rev": "sha256:1111",
            "prompt_hash": "sha256:2222",
            "artifact": artifact,
        },
        ts=ts,
    )


def confirm(device: str, target: str, ts: str | None = None) -> envelope.Event:
    return envelope.new("claim.confirmed", device, payload={"target": target}, ts=ts)


def reject(device: str, target: str, ts: str | None = None) -> envelope.Event:
    return envelope.new("claim.rejected", device, payload={"target": target}, ts=ts)


def correct(
    device: str,
    value=None,
    target: str | None = None,
    subject: str | None = None,
    predicate: str | None = None,
    ts: str | None = None,
    **extra,
) -> envelope.Event:
    """A ``claim.corrected`` event — the highest authority in the record."""
    payload: dict = {}
    if target is not None:
        payload["target"] = target
    if subject is not None:
        payload["subject"] = subject
    if predicate is not None:
        payload["predicate"] = predicate
    if value is not None:
        payload["value"] = value
    payload.update(extra)
    return envelope.new("claim.corrected", device, payload=payload, ts=ts)


def ingested(
    device: str,
    short: str = DEFAULT_ARTIFACT,
    ts: str = "2026-09-02T09:14:03Z",
    mime: str = "image/jpeg",
    path: str | None = None,
    artifact_ts: str | None = None,
    captured_ts: str | None = None,
    source: str = "camera",
) -> envelope.Event:
    """An ``artifact.ingested`` event, so citations have something to resolve to."""
    ext = {"image/jpeg": "jpg", "application/pdf": "pdf", "audio/webm": "webm"}.get(mime, "bin")
    return envelope.new(
        "artifact.ingested",
        device,
        ts=ts,
        payload={
            "hash": (short * 11)[:64],
            "short": short,
            "path": path or f"raw/2026/09/2026-09-02T0914Z_{short}.{ext}",
            "sidecar": f"raw/2026/09/2026-09-02T0914Z_{short}.{ext}.json",
            "bytes": 2048,
            "mime": mime,
            "mime_source": "sniff",
            "declared_mime": None,
            "ingested_ts": ts,
            "captured_ts": captured_ts,
            "artifact_ts": artifact_ts,
            "capture": {
                "source": source,
                "original_filename": None,
                "source_mtime_hint": None,
                "note": None,
            },
        },
    )


def merge(
    device: str, source: str, into: str, ts: str | None = None, reverted: bool = False
) -> envelope.Event:
    return envelope.new(
        "entity.merge.reverted" if reverted else "entity.merge.confirmed",
        device,
        payload={"from": source, "into": into},
        ts=ts,
    )


def on_day(day: int, hour: int = 9, month: int = 9, year: int = 2026) -> str:
    """A canonical timestamp, for streams whose ordering is the thing under test."""
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00:00Z"
