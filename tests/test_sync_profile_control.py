"""Setting ``sync_profile`` from the app, and everything that must survive it.

The setting itself is small. What is not small is the file it lives in: a
hand-written, hand-read ``config.toml`` at the root of a folder that a sync
client is free to fork at any moment. So most of what is tested here is about
the *write* rather than about the value — that comments survive, that a fork
beside the file stops the write, and that a failed write leaves the original.
"""

from __future__ import annotations

import pytest

from agent import config as config_mod
from agent.config import CONFIG_FILENAME, SyncProfile
from agent.errors import ConfigError

pytestmark = pytest.mark.usefixtures("isolated_env")


def _config(root, text: str):
    path = root / CONFIG_FILENAME
    path.write_text(text, encoding="utf-8")
    return path


# --- the rewrite -----------------------------------------------------------


def test_the_comment_explaining_the_options_survives_the_write(vault_root):
    """The whole reason this is a line rewrite and not a file rewrite."""
    path = _config(vault_root, config_mod.CONFIG_TEMPLATE)

    config_mod.set_sync_profile(path, SyncProfile.DROPBOX)
    text = path.read_text(encoding="utf-8")

    assert 'sync_profile = "dropbox"   # local | dropbox | gdrive | nextcloud | other' in text
    assert "Never put a key, token or" in text
    assert "[models.vlm]" in text
    assert "max_pixels = 1638400" in text


def test_every_other_value_is_untouched(vault_root):
    path = _config(vault_root, config_mod.CONFIG_TEMPLATE)
    before = config_mod.load(path)

    config_mod.set_sync_profile(path, SyncProfile.NEXTCLOUD)
    after = config_mod.load(path)

    assert after.sync_profile is SyncProfile.NEXTCLOUD
    assert after.port == before.port
    assert after.locale == before.locale
    assert after.raw["models"] == before.raw["models"]


def test_the_key_is_added_when_the_file_does_not_have_one(vault_root):
    path = _config(
        vault_root,
        '# my own config\nport = 7777\nlocale = "en"\n\n[models.asr]\nname = "x"\n',
    )

    config_mod.set_sync_profile(path, SyncProfile.GDRIVE)
    text = path.read_text(encoding="utf-8")

    assert config_mod.load(path).sync_profile is SyncProfile.GDRIVE
    assert text.startswith("# my own config\n")
    # Added among the top-level keys, not inside the table below them.
    assert text.index("sync_profile") < text.index("[models.asr]")


def test_a_sync_profile_inside_a_table_is_not_the_one_that_is_rewritten(vault_root):
    """A key of the same name under a table header is a different key."""
    path = _config(
        vault_root,
        'sync_profile = "local"\n\n[models.asr]\nname = "faster-whisper-small"\n',
    )

    config_mod.set_sync_profile(path, SyncProfile.OTHER)
    lines = path.read_text(encoding="utf-8").splitlines()

    assert lines[0] == 'sync_profile = "other"'
    assert lines.count('sync_profile = "other"') == 1


def test_setting_the_profile_it_already_has_writes_nothing(vault_root):
    path = _config(vault_root, config_mod.CONFIG_TEMPLATE)
    before = path.stat().st_mtime_ns

    config = config_mod.set_sync_profile(path, SyncProfile.LOCAL)

    assert config.sync_profile is SyncProfile.LOCAL
    assert path.stat().st_mtime_ns == before


# --- the guards ------------------------------------------------------------


@pytest.mark.parametrize(
    "fork",
    [
        "config (Elwood's conflicted copy 2026-09-08).toml",
        "config (1).toml",
        "config_conflict-20260908-141500.toml",
    ],
)
def test_a_sync_fork_beside_the_file_refuses_the_write(vault_root, fork):
    """Two writers already disagree; a third is how the whole file goes missing."""
    path = _config(vault_root, config_mod.CONFIG_TEMPLATE)
    (vault_root / fork).write_text('sync_profile = "local"\n', encoding="utf-8")
    original = path.read_bytes()

    with pytest.raises(ConfigError) as exc:
        config_mod.set_sync_profile(path, SyncProfile.DROPBOX)

    assert fork in str(exc.value)
    assert "merge" in str(exc.value).lower()
    assert path.read_bytes() == original


def test_the_fork_guard_ignores_the_configured_profile(vault_root):
    """A wrong profile is one of the reasons a fork exists in the first place."""
    path = _config(vault_root, 'sync_profile = "dropbox"\n')
    # A Google Drive fork, in a vault that claims to be on Dropbox.
    (vault_root / "config (1).toml").write_text("", encoding="utf-8")

    with pytest.raises(ConfigError):
        config_mod.set_sync_profile(path, SyncProfile.LOCAL)


def test_an_unrelated_numbered_file_is_not_a_fork_of_the_config(vault_root):
    path = _config(vault_root, config_mod.CONFIG_TEMPLATE)
    (vault_root / "scan (1).jpg").write_text("", encoding="utf-8")

    config_mod.set_sync_profile(path, SyncProfile.DROPBOX)

    assert config_mod.load(path).sync_profile is SyncProfile.DROPBOX


def test_a_secret_in_the_file_stops_the_write_before_it_happens(vault_root):
    path = _config(vault_root, 'sync_profile = "local"\napi_key = "sk-live-secret"\n')
    original = path.read_bytes()

    with pytest.raises(ConfigError):
        config_mod.set_sync_profile(path, SyncProfile.DROPBOX)

    assert path.read_bytes() == original


def test_a_rewrite_that_would_not_load_restores_the_original(vault_root, monkeypatch):
    """The rewrite is a regex over a hand-edited file. If it breaks it, it undoes it."""
    path = _config(vault_root, config_mod.CONFIG_TEMPLATE)
    original = path.read_bytes()
    monkeypatch.setattr(
        config_mod, "_rewrite_sync_profile", lambda text, profile: "this is not toml ["
    )

    with pytest.raises(ConfigError):
        config_mod.set_sync_profile(path, SyncProfile.DROPBOX)

    assert path.read_bytes() == original
    assert config_mod.load(path).sync_profile is SyncProfile.LOCAL


def test_there_is_no_config_to_change_is_a_refusal_not_an_invention(vault_root):
    (vault_root / CONFIG_FILENAME).unlink()

    with pytest.raises(ConfigError) as exc:
        config_mod.set_sync_profile(vault_root / CONFIG_FILENAME, SyncProfile.DROPBOX)

    assert "does not invent one" in str(exc.value)


# --- what the profile actually does ----------------------------------------


def test_the_scan_changes_behaviour_after_the_profile_changes(vault_root):
    """Not a label: the conflict patterns really do narrow to the named client."""
    from agent.events.scan import find_conflicts

    _config(vault_root, config_mod.CONFIG_TEMPLATE)
    (vault_root / "events" / "2026-09.laptop (1).jsonl").write_text("", encoding="utf-8")

    gdrive = find_conflicts(vault_root, SyncProfile.GDRIVE)
    dropbox = find_conflicts(vault_root, SyncProfile.DROPBOX)

    assert [c.path.name for c in gdrive] == ["2026-09.laptop (1).jsonl"]
    assert dropbox == []


def test_every_profile_says_what_it_changes_and_only_the_synced_ones_warn():
    for profile in SyncProfile:
        assert profile.label and profile.label[0].isupper()
        assert profile.effects, profile
        if profile is SyncProfile.LOCAL:
            assert profile.setup_warning is None
        else:
            warning = profile.setup_warning or ""
            assert "config.toml" in warning
            assert "read all of it" in warning
