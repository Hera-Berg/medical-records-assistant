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
# The constructors themselves live in ``agent.demo.authoring``: the demo command
# seeds its vault with the same shapes these tests assert on, and one definition
# of "a well-formed claim payload" is the point. Re-exported here so every test
# keeps importing them from ``.conftest``.

from agent.demo.authoring import (  # noqa: E402
    DEFAULT_ARTIFACT,
    claim,
    confirm,
    correct,
    ingested,
    merge,
    note,
    on_day,
    reject,
)

__all__ = [
    "DEFAULT_ARTIFACT",
    "claim",
    "confirm",
    "correct",
    "ingested",
    "merge",
    "note",
    "on_day",
    "proposal",
    "reject",
]
