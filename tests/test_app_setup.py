"""The first run: where the record lives, found and created without a terminal.

Someone with no coding ability should be able to press through accepting every
default and end up with a working record. So the local folder is offered first
and preselected, a new record's settings file is one a person can read and
contains nothing that points anywhere, and joining a record that already exists
writes nothing into it.
"""

from __future__ import annotations

import json

import pytest

from agent import config as config_mod
from agent import device as device_mod
from agent import firstrun
from agent import vault as vault_mod
from agent.app import locations as locations_mod
from agent.app import setup as setup_mod
from agent.config import SyncProfile
from agent.runtime import choice as choice_mod
from agent.runtime import platforms


@pytest.fixture
def home(tmp_path):
    folder = tmp_path / "home"
    (folder / "Documents").mkdir(parents=True)
    return folder


# --- where it could live ------------------------------------------------------


def test_a_folder_on_this_computer_comes_first_and_is_recommended(home):
    found = locations_mod.detect(home=home, env={}, platform="linux")
    assert found[0].key == locations_mod.LOCAL
    assert found[0].recommended
    assert found[0].profile is SyncProfile.LOCAL
    assert found[0].label == "A folder on this computer (recommended)"
    assert found[0].parent == home / "Documents"
    assert [location for location in found if location.recommended] == [found[0]]


def test_without_a_documents_folder_the_home_folder_is_offered(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert locations_mod.detect(home=bare, env={}, platform="linux")[0].parent == bare


def test_dropbox_is_found_from_its_own_info_file(home):
    personal, business = home / "Dropbox", home / "Dropbox (Work)"
    personal.mkdir()
    business.mkdir()
    (home / ".dropbox").mkdir()
    (home / ".dropbox" / "info.json").write_text(
        json.dumps({"personal": {"path": str(personal)}, "business": {"path": str(business)}})
    )
    found = locations_mod.detect(home=home, env={}, platform="linux")
    dropbox = [location for location in found if location.profile is SyncProfile.DROPBOX]
    assert [location.parent for location in dropbox] == [personal, business]
    assert found[0].key == locations_mod.LOCAL, "sync services follow the local folder"


def test_dropbox_on_windows_reads_appdata(tmp_path, home):
    appdata = tmp_path / "AppData" / "Roaming"
    (appdata / "Dropbox").mkdir(parents=True)
    folder = tmp_path / "D" / "Dropbox"
    folder.mkdir(parents=True)
    (appdata / "Dropbox" / "info.json").write_text(json.dumps({"personal": {"path": str(folder)}}))
    found = locations_mod.detect(home=home, env={"APPDATA": str(appdata)}, platform="win32", drives=())
    assert any(l.parent == folder and l.profile is SyncProfile.DROPBOX for l in found)


def test_google_drive_on_macos_is_found_under_cloud_storage(home):
    drive = home / "Library" / "CloudStorage" / "GoogleDrive-someone@example.com" / "My Drive"
    drive.mkdir(parents=True)
    found = locations_mod.detect(home=home, env={}, platform="darwin")
    assert any(l.parent == drive and l.profile is SyncProfile.GDRIVE for l in found)


def test_nextcloud_is_found_from_its_config(home):
    synced = home / "cloud" / "Nextcloud"
    synced.mkdir(parents=True)
    config = home / ".config" / "Nextcloud"
    config.mkdir(parents=True)
    (config / "nextcloud.cfg").write_text(
        "[Accounts]\n0\\Folders\\1\\localPath=" + str(synced) + "/\n0\\url=https://cloud.example\n"
    )
    found = locations_mod.detect(home=home, env={}, platform="linux")
    assert any(l.parent == synced and l.profile is SyncProfile.NEXTCLOUD for l in found)


def test_a_client_whose_folder_is_gone_is_not_offered(home):
    (home / ".dropbox").mkdir()
    (home / ".dropbox" / "info.json").write_text(json.dumps({"personal": {"path": str(home / "gone")}}))
    found = locations_mod.detect(home=home, env={}, platform="linux")
    assert [l.key for l in found] == [locations_mod.LOCAL]


def test_a_broken_client_file_is_ignored_not_raised(home):
    (home / ".dropbox").mkdir()
    (home / ".dropbox" / "info.json").write_text("{not json")
    assert locations_mod.detect(home=home, env={}, platform="linux")[0].key == locations_mod.LOCAL


def test_a_folder_inside_dropbox_is_profiled_as_dropbox(home):
    dropbox = home / "Dropbox"
    dropbox.mkdir()
    places = [
        locations_mod._local(home),
        locations_mod._dropbox_location(dropbox, "personal"),
    ]
    assert locations_mod.profile_for(dropbox / "Health record", places) is SyncProfile.DROPBOX
    assert locations_mod.profile_for(home / "Documents" / "Health record", places) is SyncProfile.LOCAL


# --- creating one -----------------------------------------------------------


def test_the_default_creates_a_working_record(home):
    plan = setup_mod.examine(str(home / "Documents"), "Health record", locations=[])
    assert plan.ok and plan.action == setup_mod.CREATE
    assert "sync_profile = \"local\"" in plan.config_text

    vault = setup_mod.carry_out(plan)

    assert vault.root == home / "Documents" / "Health record"
    assert all((vault.root / name).is_dir() for name in vault_mod.VAULT_DIRS)
    assert vault_mod.resolve_root().path == vault.root, "the pointer names it"
    assert device_mod.load().machine_matches, "this computer has an identity"
    assert firstrun.pending(), "the reader question is still to be asked"
    assert (vault.root / "config.toml").read_text() == plan.config_text, "the file shown is the file written"


def test_a_new_record_reads_on_this_computer_and_points_nowhere(home):
    vault = setup_mod.carry_out(setup_mod.examine(str(home / "Documents"), "Health record", locations=[]))
    assert "models" not in vault.config.raw
    assert choice_mod.default_for(vault) == choice_mod.THIS_COMPUTER
    assert vault.config.warnings == ()


def test_the_settings_file_explains_every_value_it_holds(home):
    text = config_mod.NEW_VAULT_CONFIG
    for key in ("sync_profile", "port", "locale"):
        line = next(n for n, row in enumerate(text.splitlines()) if row.startswith(key))
        assert text.splitlines()[line - 1].startswith("#"), f"{key} has no explanation above it"
    assert "password" in text.lower()


def test_connecting_another_computer_later_still_writes_into_it(home):
    vault = setup_mod.carry_out(setup_mod.examine(str(home / "Documents"), "Health record", locations=[]))
    path = vault.root / "config.toml"
    config_mod.set_values(
        path,
        {
            "models.vlm.base_url": "http://100.64.0.2:8080/v1",
            "models.vlm.model": "some-model",
            "models.vlm.auth.header": "Authorization",
            "models.vlm.auth.scheme": "Bearer",
        },
    )
    reloaded = config_mod.load(path)
    assert reloaded.raw["models"]["vlm"]["model"] == "some-model"
    assert "# Settings for this health record." in path.read_text(), "comments survive"


def test_a_synced_location_writes_its_profile(home):
    dropbox = home / "Dropbox"
    dropbox.mkdir()
    places = [locations_mod._dropbox_location(dropbox, "personal")]
    plan = setup_mod.examine(str(dropbox), "Health record", locations=places)
    assert plan.profile is SyncProfile.DROPBOX
    assert plan.to_dict()["warning"], "the synced warning is shown at the choice"
    assert setup_mod.carry_out(plan).profile is SyncProfile.DROPBOX


def test_an_empty_existing_folder_is_used(home):
    target = home / "Documents" / "Health record"
    target.mkdir()
    (target / ".DS_Store").write_bytes(b"\0")
    plan = setup_mod.examine(str(home / "Documents"), "Health record", locations=[])
    assert plan.action == setup_mod.CREATE


# --- joining one ------------------------------------------------------------


def test_an_existing_record_is_joined_and_nothing_in_it_is_written(home, tmp_path, monkeypatch):
    first = setup_mod.carry_out(setup_mod.examine(str(home / "Documents"), "Health record", locations=[]))
    before = {
        path.relative_to(first.root).as_posix(): path.read_bytes()
        for path in first.root.rglob("*")
        if path.is_file()
    }

    # A second computer: its own config home, no identity yet.
    monkeypatch.setenv(device_mod.ENV_CONFIG_HOME, str(tmp_path / "second-computer"))
    plan = setup_mod.examine(str(home / "Documents"), "Health record", locations=[])
    assert plan.action == setup_mod.JOIN
    assert plan.config_text is None, "no settings file is proposed for a record that has one"
    setup_mod.carry_out(plan)

    after = {
        path.relative_to(first.root).as_posix(): path.read_bytes()
        for path in first.root.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert device_mod.identity_path().is_file()
    assert vault_mod.resolve_root().path == first.root


# --- refusals ---------------------------------------------------------------


def test_a_folder_with_other_things_in_it_is_refused(home):
    target = home / "Documents" / "Taxes"
    target.mkdir()
    (target / "2025.pdf").write_bytes(b"%PDF")
    plan = setup_mod.examine(str(home / "Documents"), "Taxes", locations=[])
    assert not plan.ok
    assert "already has other things in it" in plan.refusal
    with pytest.raises(Exception):
        setup_mod.carry_out(plan)
    assert not (target / "config.toml").exists()


@pytest.mark.parametrize(
    "parent, name, words",
    [
        ("", "Health record", "Choose a folder"),
        ("Documents", "Health record", "whole path"),
        ("{home}/Documents", "", "Give the record's folder a name"),
        ("{home}/Documents", "../escape", "cannot contain slashes"),
        ("{home}/nowhere", "Health record", "There is no folder"),
    ],
)
def test_unusable_choices_are_refused_in_words(home, parent, name, words):
    plan = setup_mod.examine(parent.format(home=home), name, locations=[])
    assert not plan.ok
    assert words in plan.refusal


def test_the_readers_own_folder_is_refused(home):
    data = platforms.data_home()
    data.mkdir(parents=True, exist_ok=True)
    plan = setup_mod.examine(str(data), "Health record", locations=[])
    assert not plan.ok and "reader's own files" in plan.refusal


def test_a_record_whose_settings_will_not_load_is_refused_with_the_reason(home):
    target = home / "Documents" / "Health record"
    target.mkdir()
    (target / "config.toml").write_text('api_token = "sk-live-abcdef"\n')
    plan = setup_mod.examine(str(home / "Documents"), "Health record", locations=[])
    assert not plan.ok
    assert "could not be read" in plan.refusal


# --- identity -----------------------------------------------------------------


def test_an_identity_from_another_computer_is_kept_and_replaced_only_when_asked(home):
    device_mod.issue(label="elsewhere")
    path = device_mod.identity_path()
    data = json.loads(path.read_text())
    data["hostname"] = "some-other-machine"
    path.chmod(0o600)
    path.write_text(json.dumps(data))

    setup_mod.ensure_identity()
    assert json.loads(path.read_text())["hostname"] == "some-other-machine", "never replaced silently"

    replaced = setup_mod.replace_identity()
    assert replaced.machine_matches
    kept = list(path.parent.glob("device.replaced-*"))
    assert len(kept) == 1 and json.loads(kept[0].read_text())["id"] == data["id"]
