"""Sync forks and placeholders, per profile."""

from __future__ import annotations

import pytest

from agent import vault as vault_mod
from agent.config import SyncProfile
from agent.events import scan

DROPBOX_FORK = "2026-09.laptop-a1b2 (Elwood's conflicted copy 2026-09-08).jsonl"
NEXTCLOUD_FORK = "2026-09.laptop-a1b2_conflict-20260908-143211.jsonl"
GDRIVE_FORK = "2026-09.laptop-a1b2(1).jsonl"


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "health"
    vault_mod.scaffold(path)
    for name in (DROPBOX_FORK, NEXTCLOUD_FORK, GDRIVE_FORK, "2026-09.laptop-a1b2.jsonl"):
        (path / "events" / name).write_bytes(b"")
    return path


def names(root, profile):
    return {c.path.name for c in scan.find_conflicts(root, profile)}


def test_dropbox_profile_looks_for_dropbox_forks(root):
    assert names(root, SyncProfile.DROPBOX) == {DROPBOX_FORK}


def test_gdrive_profile_looks_for_numbered_copies(root):
    assert names(root, SyncProfile.GDRIVE) == {GDRIVE_FORK}


def test_nextcloud_profile_looks_for_its_own_and_conflicted_copies(root):
    assert names(root, SyncProfile.NEXTCLOUD) == {NEXTCLOUD_FORK, DROPBOX_FORK}


@pytest.mark.parametrize("profile", [SyncProfile.LOCAL, SyncProfile.OTHER])
def test_local_and_other_scan_everything(root, profile):
    """A local vault can still hold a fork from a restore or an earlier sync."""
    assert names(root, profile) == {DROPBOX_FORK, NEXTCLOUD_FORK, GDRIVE_FORK}


def test_a_clean_vault_reports_nothing(tmp_path):
    clean = tmp_path / "clean"
    vault_mod.scaffold(clean)
    (clean / "events" / "2026-09.laptop-a1b2.jsonl").write_bytes(b"")
    assert scan.find_conflicts(clean, SyncProfile.OTHER) == []


def test_numbered_names_outside_events_are_left_alone(root):
    """"photo (1).jpg" is an ordinary filename; only events/ pays that tax."""
    (root / "raw" / "photo (1).jpg").write_bytes(b"x")
    assert "photo (1).jpg" not in names(root, SyncProfile.OTHER)


def test_a_conflict_explains_that_its_events_are_missing(root):
    conflict = next(c for c in scan.find_conflicts(root, SyncProfile.DROPBOX))
    described = conflict.describe(root)
    assert "not in the merged view" in described
    assert "do not simply ignore it" in described


# --- placeholders -----------------------------------------------------------

def test_ordinary_file_is_not_a_placeholder(tmp_path):
    path = tmp_path / "2026-09.a.jsonl"
    path.write_bytes(b"{}\n")
    assert scan.placeholder_reason(path) is None
    assert scan.read_shard_bytes(path, SyncProfile.LOCAL).state is scan.ShardState.OK


def test_empty_file_is_empty_not_a_placeholder(tmp_path):
    path = tmp_path / "2026-09.a.jsonl"
    path.write_bytes(b"")
    assert scan.placeholder_reason(path) is None
    assert scan.read_shard_bytes(path, SyncProfile.LOCAL).state is scan.ShardState.EMPTY


def test_empty_file_on_a_virtual_drive_says_it_may_not_be_downloaded(tmp_path):
    path = tmp_path / "2026-09.a.jsonl"
    path.write_bytes(b"")
    content = scan.read_shard_bytes(path, SyncProfile.GDRIVE)
    assert content.state is scan.ShardState.EMPTY
    assert "not yet downloaded" in content.detail


@pytest.mark.parametrize("suffix", [".nextcloud", ".owncloud"])
def test_virtual_file_sidecar_marks_a_placeholder(tmp_path, suffix):
    path = tmp_path / "2026-09.a.jsonl"
    path.write_bytes(b"")
    path.with_name(path.name + suffix).write_bytes(b"")
    assert "not downloaded" in scan.placeholder_reason(path)


def test_a_small_file_is_never_guessed_to_be_a_placeholder(tmp_path):
    """Some filesystems inline small files and report zero blocks for them.

    Inferring "placeholder" from st_blocks would hide real events, so detection
    probes an actual byte instead.
    """
    path = tmp_path / "2026-09.a.jsonl"
    path.write_bytes(b"{}\n")
    assert path.stat().st_size > 0
    assert scan.placeholder_reason(path) is None


def test_unhydratable_file_is_offline_not_corrupt(tmp_path, monkeypatch):
    import errno

    path = tmp_path / "2026-09.a.jsonl"
    path.write_bytes(b"x" * 50)

    real_open = open

    def fake_open(target, mode="r", *args, **kwargs):
        if str(target).endswith(".jsonl"):
            raise OSError(errno.EIO, "Input/output error")
        return real_open(target, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    assert "could not be materialised" in scan.placeholder_reason(path)


def test_sidecar_files_are_not_themselves_scanned_as_shards(root):
    (root / "events" / "2026-09.laptop-a1b2.jsonl.nextcloud").write_bytes(b"")
    assert not any(
        c.path.name.endswith(".nextcloud") for c in scan.find_conflicts(root, SyncProfile.OTHER)
    )
