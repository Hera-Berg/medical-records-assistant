"""Shared fixtures.

Every test runs against a throwaway out-of-vault config home, so no test can
read or write the developer's real device identity or vault pointer.
"""

from __future__ import annotations

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
