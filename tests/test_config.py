"""config.toml lives in a folder that syncs to a third party. It holds no secrets."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import config as config_mod
from agent.config import SyncProfile, parse, scan_for_secrets
from agent.errors import ConfigError, SecretInConfigError

PATH = Path("/vault/config.toml")


def test_defaults_are_local_and_7777():
    config = parse({}, PATH)
    assert config.sync_profile is SyncProfile.LOCAL
    assert config.port == 7777


@pytest.mark.parametrize("name", [p.value for p in SyncProfile])
def test_every_profile_loads(name):
    assert parse({"sync_profile": name}, PATH).sync_profile.value == name


def test_unknown_profile_is_rejected():
    with pytest.raises(ConfigError, match="unknown sync_profile"):
        parse({"sync_profile": "icloud"}, PATH)


@pytest.mark.parametrize("port", [0, 70000, "7777", True, 1.5])
def test_bad_ports_are_rejected(port):
    with pytest.raises(ConfigError, match="port"):
        parse({"port": port}, PATH)


@pytest.mark.parametrize(
    "data",
    [
        {"api_key": "sk-live-abcdef"},
        {"token": "hunter2"},
        {"models": {"vlm": {"auth": {"api_key": "sk-live-abcdef"}}}},
        {"models": {"vlm": {"auth": {"secret": "x"}}}},
        {"password": "hunter2"},
        {"API_KEY": "sk-live"},
        {"access_token": "abc"},
        {"keys": ["sk-1", "sk-2"]},
        {"secrets": {"nested": "value"}},
    ],
)
def test_credentials_in_config_are_a_load_failure(data):
    # Not a warning. The file has already synced to Dropbox by the time anyone
    # reads the warning, so the value must be treated as disclosed.
    with pytest.raises(SecretInConfigError):
        parse(data, PATH)


@pytest.mark.parametrize(
    "data",
    [
        {"models": {"vlm": {"auth": {"api_key_env": "HEALTH_VLM_TOKEN"}}}},
        {"models": {"vlm": {"auth": {"header": "Authorization", "scheme": "Bearer"}}}},
        {"models": {"vlm": {"auth": {"header": "X-API-Key", "scheme": ""}}}},
        {"api_key": ""},  # explicitly unset
        {"api_key": "   "},
        {"models": {"asr": {"sha256": "abc123"}}},
    ],
)
def test_references_to_a_secret_are_allowed(data):
    assert parse(data, PATH) is not None


def test_secret_scan_reports_the_dotted_path():
    assert scan_for_secrets({"models": {"vlm": {"api_key": "sk"}}}) == ["models.vlm.api_key"]


def test_models_table_is_carried_but_not_validated():
    config = parse({"models": {"vlm": {"base_url": "http://100.1.2.3:8080/v1"}}}, PATH)
    assert config.raw["models"]["vlm"]["base_url"] == "http://100.1.2.3:8080/v1"
    assert config.warnings == ()


def test_vault_path_in_config_is_ignored_with_an_explanation():
    config = parse({"vault_path": "/somewhere"}, PATH)
    assert any("vault root is resolved" in w for w in config.warnings)


def test_load_reads_a_real_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('sync_profile = "dropbox"\nport = 8000\n', encoding="utf-8")
    config = config_mod.load(path)
    assert config.sync_profile is SyncProfile.DROPBOX
    assert config.port == 8000


def test_missing_config_names_the_file_and_prints_a_template(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        config_mod.load(tmp_path / "config.toml")
    assert 'sync_profile = "local"' in str(excinfo.value)


def test_invalid_toml_is_reported_as_such(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is not = = toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        config_mod.load(path)


def test_only_local_skips_readback_verification():
    assert SyncProfile.LOCAL.verify_readback is False
    for profile in (SyncProfile.DROPBOX, SyncProfile.GDRIVE, SyncProfile.NEXTCLOUD,
                    SyncProfile.OTHER):
        assert profile.verify_readback is True


def test_only_local_has_no_setup_warning():
    assert SyncProfile.LOCAL.setup_warning is None
    warning = SyncProfile.DROPBOX.setup_warning
    # Both halves of the warning, because either alone is misleading: what the
    # account can see, and that a share of it cannot be taken back.
    assert "read all of it" in warning
    assert "cannot reliably be taken back" in warning
