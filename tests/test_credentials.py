"""Where the key may come from, and what happens when it is wrong.

``MODELS.md`` names four credential tests as "cheap and catch the failures that
matter most". Three of them live here; ``test_key_never_written_to_vault`` needs
a full extraction run and lives with the runner.

The rule the user added to the plan is the one most of this file is about: **an
empty resolved credential fails at startup with a clear message, not a blank
header on the wire.** An empty environment variable that fell through to the
keychain would silently use a key the user thought they had overridden, and an
empty value that went out would come back as a 401 that looks exactly like a
rotated key. Both hide a one-line misconfiguration behind a confusing symptom.
"""

from __future__ import annotations

import logging
import os
import stat

import pytest

from agent.errors import CredentialError
from agent.llm import credentials, redaction

KEY = "sk-tailnet-0123456789abcdef"
ENV_NAME = "HEALTH_VLM_TOKEN_TEST"

#: Captured before the autouse fixture stubs it out, so the few tests that
#: exercise the real keychain reader can put it back.
_REAL_FROM_KEYCHAIN = credentials._from_keychain


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    # The keychain is a real OS service. Nothing in this suite may read or write
    # the developer's own, so it is always stubbed to "no entry" unless a test
    # says otherwise.
    monkeypatch.setattr(credentials, "_from_keychain", lambda: None)
    redaction.forget_all()
    yield
    redaction.forget_all()


@pytest.fixture
def credentials_file(tmp_path):
    path = tmp_path / "credentials"
    path.write_text(KEY + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


# --- resolution order ------------------------------------------------------


def test_the_environment_wins_over_everything(monkeypatch, credentials_file):
    monkeypatch.setenv(ENV_NAME, "sk-from-the-environment")
    found = credentials.resolve(ENV_NAME, path=credentials_file)

    assert found.value == "sk-from-the-environment"
    assert ENV_NAME in found.source


def test_the_file_is_read_when_nothing_earlier_answers(credentials_file):
    found = credentials.resolve(ENV_NAME, path=credentials_file)

    assert found.value == KEY
    assert found.source == credentials.SOURCE_FILE


def test_the_keychain_sits_between_them(monkeypatch, credentials_file):
    monkeypatch.setattr(
        credentials,
        "_from_keychain",
        lambda: credentials.Credential("sk-from-the-keychain", credentials.SOURCE_KEYCHAIN),
    )
    found = credentials.resolve(ENV_NAME, path=credentials_file)

    assert found.value == "sk-from-the-keychain"


def test_a_key_line_may_carry_a_name_equals_prefix(tmp_path):
    path = tmp_path / "credentials"
    path.write_text(f"# the box\nHEALTH_VLM_TOKEN = \"{KEY}\"\n", encoding="utf-8")
    path.chmod(0o600)

    assert credentials.resolve(ENV_NAME, path=path).value == KEY


# --- an empty credential is a misconfiguration, not a credential -----------


def test_an_empty_environment_variable_fails_rather_than_falling_through(
    monkeypatch, credentials_file
):
    """Falling through would use a key the user believed they had overridden."""
    monkeypatch.setenv(ENV_NAME, "")

    with pytest.raises(CredentialError) as raised:
        credentials.resolve(ENV_NAME, path=credentials_file)

    message = str(raised.value)
    assert ENV_NAME in message, "names the source, so the fix is one line"
    assert "blank Authorization header" in message
    assert "looks like a rotated key" in message


def test_a_whitespace_only_environment_variable_is_also_empty(monkeypatch):
    monkeypatch.setenv(ENV_NAME, "   \n")
    with pytest.raises(CredentialError, match="set but empty"):
        credentials.resolve(ENV_NAME)


class _FakeKeyring:
    """Stands in for the `keyring` module, which is an optional dependency."""

    def __init__(self, value):
        self.value = value
        self.asked: list[tuple[str, str]] = []

    def get_password(self, service, account):
        self.asked.append((service, account))
        return self.value


def _with_keyring(monkeypatch, value):
    fake = _FakeKeyring(value)
    monkeypatch.setitem(__import__("sys").modules, "keyring", fake)
    monkeypatch.setattr(credentials, "_from_keychain", _REAL_FROM_KEYCHAIN)
    return fake


def test_an_empty_keychain_entry_fails(monkeypatch):
    """Same rule as the environment: present-but-empty says so and stops."""
    _with_keyring(monkeypatch, "   ")

    with pytest.raises(CredentialError) as raised:
        credentials.resolve(ENV_NAME)

    message = str(raised.value)
    assert "exists but is empty" in message
    assert "never sent" in message


def test_the_keychain_is_asked_under_the_documented_service_and_account(monkeypatch):
    fake = _with_keyring(monkeypatch, KEY)

    assert credentials.resolve(ENV_NAME).value == KEY
    assert fake.asked == [(credentials.KEYRING_SERVICE, credentials.KEYRING_ACCOUNT)]


def test_a_missing_keyring_package_is_skipped_not_fatal(monkeypatch, credentials_file):
    """Without it the env var and the file still work."""
    monkeypatch.setitem(__import__("sys").modules, "keyring", None)
    monkeypatch.setattr(credentials, "_from_keychain", _REAL_FROM_KEYCHAIN)

    # `None` in sys.modules makes `import keyring` raise ImportError, which is
    # exactly the shape of the package not being installed.
    assert credentials.resolve(ENV_NAME, path=credentials_file).value == KEY


def test_an_empty_credentials_file_fails(tmp_path):
    path = tmp_path / "credentials"
    path.write_text("# only a comment\n\n", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(CredentialError, match="holds no key"):
        credentials.resolve(ENV_NAME, path=path)


def test_no_credential_anywhere_says_where_it_looked(tmp_path):
    with pytest.raises(CredentialError) as raised:
        credentials.resolve(ENV_NAME, path=tmp_path / "absent")

    message = str(raised.value)
    assert "no credential found" in message
    assert "health-agent set-key" in message
    assert "cannot go in config.toml" in message


# --- the file's own guards -------------------------------------------------


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666, 0o700])
def test_a_credentials_file_wider_than_0600_is_refused(tmp_path, mode):
    path = tmp_path / "credentials"
    path.write_text(KEY, encoding="utf-8")
    path.chmod(mode)

    with pytest.raises(CredentialError) as raised:
        credentials.resolve(ENV_NAME, path=path)

    message = str(raised.value)
    assert "chmod 600" in message
    assert KEY not in message, "and a refusal never echoes the key"


def test_a_credentials_file_inside_the_vault_is_refused(tmp_path):
    """The vault syncs. A key inside it has already been uploaded."""
    vault = tmp_path / "health"
    vault.mkdir()
    path = vault / "credentials"
    path.write_text(KEY, encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(CredentialError) as raised:
        credentials.resolve(ENV_NAME, vault_root=vault, path=path)

    assert "inside the vault" in str(raised.value)
    assert "rotate the key" in str(raised.value)


def test_a_credentials_file_outside_the_vault_is_fine(tmp_path, credentials_file):
    vault = tmp_path / "health"
    vault.mkdir()
    assert credentials.resolve(ENV_NAME, vault_root=vault, path=credentials_file).value == KEY


# --- what may be reported --------------------------------------------------


def test_status_reports_ok_and_the_source_but_never_the_key(monkeypatch):
    monkeypatch.setenv(ENV_NAME, KEY)
    state, source = credentials.status(ENV_NAME)

    assert state == "ok"
    assert KEY not in str(source)
    assert KEY[:8] not in str(source), "not even a prefix"


def test_status_distinguishes_missing_from_broken(tmp_path, monkeypatch):
    assert credentials.status(ENV_NAME, path=tmp_path / "absent")[0] == "missing"

    monkeypatch.setenv(ENV_NAME, "")
    assert credentials.status(ENV_NAME, path=tmp_path / "absent")[0] == "error"


def test_a_credential_never_reprs_its_value():
    """A dataclass repr reaches log lines and debugger output for free."""
    found = credentials.Credential(KEY, "test")

    assert KEY not in repr(found)
    assert "redacted" in repr(found)


# --- redaction -------------------------------------------------------------


def test_key_redacted_from_logs_and_errors(caplog):
    """MODELS.md names this one. httpx will put headers in an error repr."""
    redaction.register(KEY)
    logger = redaction.install("agent.test")

    with caplog.at_level(logging.ERROR, logger="agent.test"):
        logger.error("request failed: headers={'Authorization': 'Bearer %s'}", KEY)

    written = caplog.text
    assert KEY not in written
    assert redaction.REDACTED in written


def test_an_exception_message_is_scrubbed_too():
    redaction.register(KEY)
    message = f"Client error for url https://box/v1?key={KEY} with Bearer {KEY}"

    scrubbed = redaction.scrub(message)
    assert KEY not in scrubbed
    assert scrubbed.count(redaction.REDACTED) == 2


def test_a_percent_encoded_key_is_scrubbed():
    """A key that reached a URL does not always arrive verbatim."""
    secret = "sk-with/slashes+and=signs"
    redaction.register(secret)
    from urllib.parse import quote

    assert secret not in redaction.scrub(f"?token={quote(secret, safe='')}")


def test_a_short_placeholder_is_not_registered_as_a_secret():
    """Scrubbing a three-character 'secret' would redact half of every log line."""
    redaction.forget_all()
    redaction.register("abc")

    assert redaction.known() == frozenset()
    assert redaction.scrub("abc def") == "abc def"


def test_assert_absent_is_the_check_of_last_resort():
    redaction.register(KEY)
    redaction.assert_absent("nothing to see", "a test")

    with pytest.raises(AssertionError, match="rotate it"):
        redaction.assert_absent(f"Bearer {KEY}", "a test")


def test_the_filter_is_installed_only_once():
    logger = redaction.install("agent.idempotent")
    redaction.install("agent.idempotent")

    filters = [f for f in logger.filters if isinstance(f, redaction.SecretFilter)]
    assert len(filters) == 1


def test_the_credentials_path_sits_beside_the_device_identity():
    """Both are deliberately outside the vault, so both survive it being moved."""
    from agent import device as device_mod

    path = credentials.credentials_path()

    assert path.name == "credentials"
    assert path.parent == device_mod.config_home()


def test_the_file_mode_constant_is_actually_owner_only():
    assert stat.S_IMODE(credentials.MAX_FILE_MODE) == 0o600
    assert not credentials.MAX_FILE_MODE & (stat.S_IRWXG | stat.S_IRWXO)


def test_resolution_reads_the_environment_every_time(monkeypatch):
    """Rotation without restart: one failed job, not a puzzled half hour."""
    monkeypatch.setenv(ENV_NAME, "first")
    assert credentials.resolve(ENV_NAME).value == "first"

    os.environ[ENV_NAME] = "second"
    assert credentials.resolve(ENV_NAME).value == "second"
