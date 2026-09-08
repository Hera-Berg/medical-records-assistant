"""Device identity lives outside the vault, so it survives the folder moving."""

from __future__ import annotations

import json
import shutil

import pytest

from agent import device as device_mod
from agent import vault as vault_mod
from agent.config import CONFIG_FILENAME
from agent.errors import DeviceIdentityError, DeviceIdentityMismatch
from agent.vault import Vault

from .conftest import MINIMAL_CONFIG, note


def test_identity_is_stored_outside_the_vault(vault_root):
    identity = device_mod.issue()
    assert identity.path is not None
    assert vault_root not in identity.path.parents
    assert identity.path.name == "device"


def test_identity_file_records_the_documented_fields():
    identity = device_mod.issue(label="elwood-laptop")
    data = json.loads(identity.path.read_text(encoding="utf-8"))
    assert set(data) == {"id", "label", "created", "hostname", "platform"}
    assert data["label"] == "elwood-laptop"
    assert data["id"].startswith("elwood-laptop-")


def test_identity_file_is_owner_only():
    identity = device_mod.issue()
    assert identity.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "label",
    ["elwood-laptop", "Elwood's MacBook Pro", "ELWOOD  LAPTOP", "日本語", "a" * 60],
)
def test_ids_stay_inside_the_filename_charset(label):
    identity = device_mod.issue(label=label)
    assert device_mod.is_valid_device_id(identity.id)
    assert len(identity.id) <= device_mod.DEVICE_ID_MAX_LENGTH
    assert set(identity.id) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")


def test_label_override_replaces_the_hostname_in_the_id(monkeypatch):
    # The hostname ends up in filenames inside a folder that may sync to a third
    # party, so it must be possible to keep it out.
    monkeypatch.setenv(device_mod.ENV_LABEL, "kitchen-tablet")
    identity = device_mod.issue()
    assert identity.id.startswith("kitchen-tablet-")
    assert device_mod.current_hostname() not in identity.id


def test_issue_never_overwrites_an_existing_identity():
    first = device_mod.issue()
    with pytest.raises(DeviceIdentityError, match="already exists"):
        device_mod.issue()
    assert device_mod.load().id == first.id


def test_env_override_bypasses_the_file(monkeypatch):
    device_mod.issue()
    monkeypatch.setenv(device_mod.ENV_DEVICE, "phone-9xyz")
    identity = device_mod.load()
    assert identity.id == "phone-9xyz"
    assert identity.source == "env"


@pytest.mark.parametrize("bad", ["Phone", "phone_9", "phone 9", "a" * 25, "", "phone-"])
def test_env_override_is_validated(monkeypatch, bad):
    monkeypatch.setenv(device_mod.ENV_DEVICE, bad)
    with pytest.raises(DeviceIdentityError):
        device_mod.load()


def test_missing_identity_explains_why_it_is_not_in_config():
    with pytest.raises(DeviceIdentityError, match="outside the vault"):
        device_mod.load()


def test_corrupt_identity_file_is_reported(isolated_env):
    path = device_mod.identity_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(DeviceIdentityError, match="not valid JSON"):
        device_mod.load()


def _clone_identity_from_another_machine(identity, hostname="someone-elses-imac"):
    data = json.loads(identity.path.read_text(encoding="utf-8"))
    data["hostname"] = hostname
    identity.path.write_text(json.dumps(data), encoding="utf-8")


def test_cloned_identity_is_detected_deterministically(identity):
    _clone_identity_from_another_machine(identity)
    reloaded = device_mod.load()
    assert reloaded.machine_matches is False
    assert "cloned or restored" in reloaded.mismatch_reason()


def test_cloned_identity_refuses_appends_but_still_allows_reads(vault_root, identity):
    vault = Vault.open(vault_root, identity=identity)
    vault.append(note(identity.id))

    _clone_identity_from_another_machine(identity)
    restored = Vault.open(vault_root, identity=device_mod.load())

    with pytest.raises(DeviceIdentityMismatch):
        restored.append(note(identity.id))
    # Reading a restored vault must still work; you have to be able to look at it.
    assert len(restored.read().events) == 1


def test_platform_change_also_counts_as_a_clone(identity):
    data = json.loads(identity.path.read_text(encoding="utf-8"))
    data["platform"] = "Plan9"
    identity.path.write_text(json.dumps(data), encoding="utf-8")
    assert device_mod.load().machine_matches is False


def test_env_override_rescues_a_cloned_identity(monkeypatch, identity):
    _clone_identity_from_another_machine(identity)
    monkeypatch.setenv(device_mod.ENV_DEVICE, "restored-7ab2")
    reloaded = device_mod.load()
    assert reloaded.machine_matches is True
    reloaded.require_appendable()


@pytest.mark.parametrize(
    "recorded", ["Elwood-Laptop", "elwood-laptop.local", "elwood-laptop.", "ELWOOD-LAPTOP.lan"]
)
def test_hostname_comparison_normalises_before_matching(identity, monkeypatch, recorded):
    # macOS flaps between "foo" and "foo.local"; that must not read as a clone.
    monkeypatch.setattr(device_mod.socket, "gethostname", lambda: "elwood-laptop")
    data = json.loads(identity.path.read_text(encoding="utf-8"))
    data["hostname"] = recorded
    identity.path.write_text(json.dumps(data), encoding="utf-8")
    assert device_mod.load().machine_matches is True


def test_identity_survives_the_vault_being_moved(tmp_path, identity):
    """The whole reason the identity is not in config.toml.

    The vault folder is expected to be dragged into Dropbox at some point. The
    device must keep its id and keep appending to the same shard afterwards.
    """
    original = tmp_path / "health"
    vault_mod.scaffold(original)
    (original / CONFIG_FILENAME).write_text(MINIMAL_CONFIG, encoding="utf-8")
    vault = Vault.open(original, identity=identity)
    shard = vault.append(note(identity.id))

    moved = tmp_path / "Dropbox" / "health"
    moved.parent.mkdir()
    shutil.move(str(original), str(moved))

    reopened = Vault.open(moved)
    assert reopened.identity.id == identity.id
    assert reopened.identity.machine_matches
    reopened.append(note(identity.id))

    read = reopened.read()
    assert len(read.events) == 2
    assert read.is_clean
    # Same device, so still exactly one shard with the same name.
    assert [s.path.name for s in read.shards] == [shard.name]
