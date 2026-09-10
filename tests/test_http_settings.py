"""``/api/settings`` — the storage profile, over HTTP.

The route is thin. What it is asserted on here is what it *says*, because this
is the one screen where a wrong mental model has a real cost: someone who
believes picking "Dropbox" moves their record into Dropbox will pick it while
their record sits on a laptop, and someone who believes it does not matter will
leave it on ``local`` while their record sits in a shared folder. So the copy is
tested like behaviour — the explanation rules out the wrong reading, and the
warning names the account and the credential mistake.
"""

from __future__ import annotations

from agent import config as config_mod
from agent.config import CONFIG_FILENAME, SyncProfile


def test_the_settings_route_says_it_moves_nothing(client):
    body = client.get("/api/settings").json()

    explanation = body["explanation"].lower()
    assert "does not move anything" in explanation
    assert "never signs in" in explanation
    assert body["sync_profile"]["current"] == "local"
    assert [option["value"] for option in body["sync_profile"]["options"]] == [
        profile.value for profile in SyncProfile
    ]


def test_every_option_carries_what_it_changes_and_the_synced_ones_warn(client):
    options = client.get("/api/settings").json()["sync_profile"]["options"]

    for option in options:
        assert option["effects"], option["value"]
        if option["value"] == "local":
            assert option["warning"] is None
        else:
            assert "config.toml" in option["warning"]


def test_choosing_a_synced_profile_changes_the_file_and_returns_the_warning(
    client, vault
):
    response = client.post("/api/settings/sync-profile", json={"profile": "dropbox"})

    assert response.status_code == 200
    body = response.json()
    assert body["changed"] == "dropbox"
    assert body["sync_profile"]["current"] == "dropbox"
    assert "Dropbox" in body["sync_profile"]["warning"]
    # And the file on disk actually says so, which is the whole point.
    assert (
        config_mod.load(vault.root / CONFIG_FILENAME).sync_profile
        is SyncProfile.DROPBOX
    )


def test_the_running_server_uses_the_new_profile_immediately(client, vault):
    """No restart. The next append and the next scan both use the new profile."""
    client.post("/api/settings/sync-profile", json={"profile": "gdrive"})

    assert vault.profile is SyncProfile.GDRIVE
    assert client.get("/api/health").json()["vault"]["sync_profile"] == "gdrive"


def test_a_fork_the_new_profile_cares_about_is_reported_on_the_same_response(
    client, vault
):
    (vault.root / "events" / "2026-09.laptop (1).jsonl").write_text("", encoding="utf-8")

    body = client.post("/api/settings/sync-profile", json={"profile": "gdrive"}).json()

    assert any("2026-09.laptop (1).jsonl" in line for line in body["conflicts"])


def test_an_unknown_profile_is_refused_by_name(client):
    response = client.post("/api/settings/sync-profile", json={"profile": "icloud"})

    assert response.status_code == 400
    assert "icloud" in response.json()["detail"]
    assert "nextcloud" in response.json()["detail"]


def test_a_forked_config_is_a_conflict_the_user_is_told_to_merge(client, vault):
    (vault.root / "config (1).toml").write_text("", encoding="utf-8")
    original = (vault.root / CONFIG_FILENAME).read_bytes()

    response = client.post("/api/settings/sync-profile", json={"profile": "dropbox"})

    assert response.status_code == 409
    assert "config (1).toml" in response.json()["detail"]
    assert (vault.root / CONFIG_FILENAME).read_bytes() == original
    assert client.get("/api/settings").json()["config"]["conflict_forks"] == [
        "config (1).toml"
    ]


def test_the_settings_route_returns_no_credential_of_any_kind(client, monkeypatch):
    """The same rule as /api/health: no key, no prefix, no length."""
    monkeypatch.setenv("HEALTH_VLM_TOKEN", "sk-live-not-in-any-response")

    body = client.get("/api/settings").text
    changed = client.post("/api/settings/sync-profile", json={"profile": "other"}).text

    for text in (body, changed):
        assert "sk-live" not in text
        assert "HEALTH_VLM_TOKEN" not in text
