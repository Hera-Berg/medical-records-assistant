"""The inference endpoint, set from the settings screen.

Nothing here is new capability: the address guard, credential resolution, the
keychain write and the startup probe are all phase-4 code. What is new is that a
person reaches them without editing TOML, and that is where the failures worth
testing are.

Four rules do most of the work below.

**The key is set-only.** No route returns it, a prefix of it, or its length —
including the ones added here, including their error paths. ``MODELS.md`` names
that test; this file is where the new routes join it.

**It never goes into ``config.toml``.** That file syncs with the vault, so a key
written there has been handed to a third party by definition. The write path
here touches four keys and none of them is a credential.

**The guard runs at save time, not at first use.** A public address is refused
when it is typed, with the reason, rather than days later when a photograph
fails to be read.

**Every state is reported as itself.** Unreachable, unauthorised and working are
three different problems with three different answers, and the test button is
the one screen whose whole job is telling them apart. The vision step matters
most: a box that silently discards image content parts passes every other check
and ignores every prescription photo.
"""

from __future__ import annotations

import json
import sys

import httpx
import pytest

from agent.config import CONFIG_FILENAME
from agent.llm import credentials as credentials_mod
from agent.llm import redaction
from agent.server import endpoint_check
from agent.extract import probe as probe_mod

from .conftest import JPEG, api_client

KEY = "sk-endpoint-settings-must-never-be-echoed-4417"
MODEL = "Jundot/Qwen3.8-Flash-Next-oQ4e-mtp"
#: Loopback is inside the allowed address space and resolves without DNS, so
#: every test here runs with no network and no resolver stub.
URL = "http://127.0.0.1:7999/v1"


#: Captured at import, before any fixture stubs it, so the tests that exercise
#: the real keychain reader can put it back.
_REAL_FROM_KEYCHAIN = credentials_mod._from_keychain


@pytest.fixture(autouse=True)
def _a_key_exists(monkeypatch):
    """A resolvable credential, so the probe gets past its credential step.

    From the keychain, which is where ``set-key`` puts one and therefore what a
    vault that has never been hand-edited actually uses: ``api_key_env`` is only
    consulted when ``config.toml`` names a variable, and a fresh vault does not.
    """
    monkeypatch.setattr(
        credentials_mod,
        "_from_keychain",
        lambda: credentials_mod.Credential(KEY, credentials_mod.SOURCE_KEYCHAIN),
    )
    redaction.forget_all()
    yield
    redaction.forget_all()


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.setattr(credentials_mod, "_from_keychain", lambda: None)


# --- a box, behaving in one of the ways a real one does --------------------


def _answer(content: str, model: str = MODEL, finish: str = "stop") -> dict:
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content},
             "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 8},
    }


def box(
    monkeypatch,
    *,
    models: list[str] | None = None,
    status: int | None = None,
    sees_images: bool = True,
    honours_schema: bool = True,
    model: str = MODEL,
    refuse: bool = False,
):
    """Stand in for the inference box, badly behaved to order.

    Replaces :func:`agent.server.endpoint_check.open_client`, which exists as a
    seam for exactly this. Everything above it — the probe, the guard, the
    credential resolution, the classification of a 401 as terminal — is the real
    code and runs unchanged.
    """
    from agent.llm.client import Client

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if refuse:
            raise httpx.ConnectError("connection refused", request=request)
        if status is not None:
            # The body carries the key, which is what a real server's error
            # echo looks like. Nothing in the response to the browser may
            # contain it.
            return httpx.Response(
                status, json={"error": f"rejected token {KEY}", "sent": KEY}
            )
        if request.url.path.endswith("/models"):
            listed = models if models is not None else [MODEL]
            return httpx.Response(
                200, json={"data": [{"id": name} for name in listed]}
            )
        body = json.loads(request.content)
        if body.get("response_format"):
            content = '{"word": "ready"}' if honours_schema else "Certainly! Here goes:"
            return httpx.Response(200, json=_answer(content, model))
        has_image = any(
            part.get("type") == "image_url"
            for message in body["messages"]
            for part in (message["content"] if isinstance(message["content"], list) else [])
        )
        if has_image and sees_images:
            return httpx.Response(200, json=_answer(probe_mod.PROBE_TEXT, model))
        return httpx.Response(200, json=_answer("a picture of something", model))

    def open_client(vault, settings):
        return Client(
            settings, vault_root=vault.root, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(endpoint_check, "open_client", open_client)
    return seen


def draft(**overrides) -> dict:
    return {"base_url": URL, "model": MODEL, **overrides}


# --- what the screen is handed ---------------------------------------------


def test_a_vault_with_no_endpoint_says_so_and_offers_the_defaults(client):
    endpoint = client.get("/api/settings").json()["endpoint"]

    assert endpoint["configured"] is False
    assert endpoint["base_url"] == ""
    assert endpoint["model"] == ""
    assert endpoint["auth"] == {
        "header": "Authorization",
        "scheme": "Bearer",
        "api_key_env": None,
    }
    assert endpoint["problem"] is None


def test_the_explanation_rules_out_the_reading_that_this_is_a_service(client):
    """Not a sign-up. A machine the person already owns, and nothing else."""
    explanation = client.get("/api/settings").json()["endpoint"]["explanation"].lower()

    assert "a computer you own" in explanation
    assert "will not talk to anything outside your own network" in explanation
    # And the sentence that stops a sleeping box reading as a broken record.
    assert "nothing breaks" in explanation


def test_the_key_field_says_where_a_key_was_found_and_never_what_it_is(client):
    key = client.get("/api/settings").json()["endpoint"]["key"]

    assert key["state"] == "configured"
    assert key["source"] == "keychain", "the place, never the value"
    assert KEY not in json.dumps(key)


def test_an_exported_variable_shadowing_the_keychain_is_visible(client, vault, monkeypatch):
    """The trap: the environment wins, so storing a new key can change nothing.

    Reporting *which* place answered is what makes that five seconds of
    confusion rather than an afternoon of it.
    """
    from agent import config as config_mod

    (vault.root / CONFIG_FILENAME).write_text(
        config_mod.CONFIG_TEMPLATE.replace(
            "https://macbook-pro.tailnet.ts.net/v1", URL
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HEALTH_VLM_TOKEN", "sk-exported-and-winning")
    client.app.state.record.reload_config()

    key = client.get("/api/settings").json()["endpoint"]["key"]

    assert key["state"] == "configured"
    assert "HEALTH_VLM_TOKEN" in key["source"]
    assert "sk-exported" not in json.dumps(key)


def test_the_key_explanation_says_why_it_cannot_go_in_the_settings_file(client):
    explanation = client.get("/api/settings").json()["endpoint"]["key_explanation"]

    assert "keychain" in explanation
    assert "settings file" in explanation
    assert "Dropbox" in explanation


def test_no_key_set_is_not_set_rather_than_an_error(client, no_key):
    key = client.get("/api/settings").json()["endpoint"]["key"]
    assert key == {"state": "not set", "source": None, "detail": None}


# --- saving ----------------------------------------------------------------


def test_saving_writes_four_keys_and_the_running_server_uses_them(client, vault):
    response = client.post("/api/settings/endpoint", json=draft())

    assert response.status_code == 200
    endpoint = response.json()["endpoint"]
    assert endpoint["configured"] is True
    assert endpoint["base_url"] == URL
    assert endpoint["model"] == MODEL

    text = (vault.root / CONFIG_FILENAME).read_text(encoding="utf-8")
    assert f'base_url = "{URL}"' in text
    assert f'model = "{MODEL}"' in text
    assert '[models.vlm.auth]' in text
    # And the next read of the settings — a fresh load of the file — agrees.
    assert client.get("/api/settings").json()["endpoint"]["base_url"] == URL


def test_the_model_id_is_stored_exactly_as_the_box_reports_it(client, vault):
    """``Jundot/Qwen…`` is not ``Qwen…``, and identity is checked on every call."""
    client.post("/api/settings/endpoint", json=draft())

    from agent.extract import session

    assert session.settings_for(vault).vlm.model == MODEL
    assert "Jundot/" in (vault.root / CONFIG_FILENAME).read_text(encoding="utf-8")


def test_the_comments_in_the_settings_file_survive_the_write(client, vault):
    """The reason this is a line rewrite rather than a TOML round-trip."""
    path = vault.root / CONFIG_FILENAME
    from agent import config as config_mod

    path.write_text(config_mod.CONFIG_TEMPLATE, encoding="utf-8")

    client.post("/api/settings/endpoint", json=draft())
    text = path.read_text(encoding="utf-8")

    assert "Never put a key, token or" in text
    assert "# must resolve to 100.x / RFC1918" in text
    assert "# copy verbatim from /v1/models" in text
    assert "max_pixels = 1638400" in text, "and every value not on the screen"


def test_a_public_address_is_refused_with_the_reason(client, vault):
    """Not a validation error. The premise of the project is the reason."""
    before = (vault.root / CONFIG_FILENAME).read_bytes()

    response = client.post(
        "/api/settings/endpoint", json=draft(base_url="https://93.184.216.34/v1")
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "outside private address space" in detail
    assert "never leaves infrastructure you control" in detail
    assert "fork the project" in detail
    assert (vault.root / CONFIG_FILENAME).read_bytes() == before


def test_a_key_pasted_into_the_url_is_refused_and_named_as_disclosed(client):
    response = client.post(
        "/api/settings/endpoint", json=draft(base_url="https://me:hunter2@127.0.0.1/v1")
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "username or password" in detail
    assert "syncs to Dropbox" in detail
    assert "rotate it" in detail


def test_saving_without_choosing_a_model_says_to_fetch_the_list(client):
    response = client.post("/api/settings/endpoint", json=draft(model=""))

    assert response.status_code == 400
    assert "Fetch the list" in response.json()["detail"]


def test_an_empty_scheme_is_a_real_answer_and_reaches_the_header(client, vault):
    """Some self-hosted servers want ``X-API-Key: <key>`` with no word in front."""
    client.post(
        "/api/settings/endpoint",
        json=draft(header="X-API-Key", scheme=""),
    )

    auth = client.get("/api/settings").json()["endpoint"]["auth"]
    assert auth == {"header": "X-API-Key", "scheme": "", "api_key_env": None}

    from agent.extract import session

    assert session.settings_for(vault).vlm.auth.format("abc") == ("X-API-Key", "abc")


def test_a_forked_settings_file_refuses_the_write(client, vault):
    (vault.root / "config (1).toml").write_text("", encoding="utf-8")
    before = (vault.root / CONFIG_FILENAME).read_bytes()

    response = client.post("/api/settings/endpoint", json=draft())

    assert response.status_code == 409
    assert "config (1).toml" in response.json()["detail"]
    assert (vault.root / CONFIG_FILENAME).read_bytes() == before


def test_saving_a_new_endpoint_forgets_what_was_known_about_the_old_one(client, vault):
    """A green tick from ten minutes ago must not sit under a new address."""
    from agent.errors import AuthRejected

    state = client.app.state.record
    state.record_endpoint_error(AuthRejected("nope"))
    assert state.endpoint.state == "unauthorised"

    client.post("/api/settings/endpoint", json=draft())

    assert client.get("/api/health").json()["endpoint"]["state"] == "unknown"


# --- the key ---------------------------------------------------------------


class _FakeKeyring:
    def __init__(self):
        self.stored: dict[tuple[str, str], str] = {}

    def set_password(self, service, account, value):
        self.stored[(service, account)] = value

    def get_password(self, service, account):
        return self.stored.get((service, account))


@pytest.fixture
def keychain(monkeypatch):
    """A keychain that is not the developer's, with the real reader put back.

    The reader is genuine — it is the fake ``keyring`` module below that stands
    in for the OS service — so a key stored through the route is read back the
    way the running app would read it.
    """
    fake = _FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setattr(credentials_mod, "_from_keychain", _REAL_FROM_KEYCHAIN)
    return fake


def test_a_key_goes_to_the_keychain_and_never_into_the_vault(client, vault, keychain):
    response = client.post("/api/settings/endpoint/key", json={"key": KEY})

    assert response.status_code == 200
    assert keychain.stored[
        (credentials_mod.KEYRING_SERVICE, credentials_mod.KEYRING_ACCOUNT)
    ] == KEY

    for path in sorted(vault.root.rglob("*")):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), f"a key reached {path}"


def test_the_response_to_setting_a_key_contains_no_part_of_it(client, keychain):
    body = client.post("/api/settings/endpoint/key", json={"key": KEY}).text

    assert KEY not in body
    for length in range(12, len(KEY) + 1):
        assert KEY[:length] not in body


def test_setting_a_key_says_where_it_will_actually_be_read_from(client, keychain):
    """The environment beats the keychain, and a person has to be told so."""
    body = client.post("/api/settings/endpoint/key", json={"key": "sk-a-different-one"}).json()

    assert "keychain" in body["stored"]
    # The autouse fixture exports HEALTH_VLM_TOKEN, but this vault names no
    # api_key_env, so the keychain is what answers.
    assert body["endpoint"]["key"]["state"] == "configured"
    assert "keychain" in body["endpoint"]["key"]["source"]


def test_an_empty_key_is_refused_rather_than_stored(client, keychain):
    response = client.post("/api/settings/endpoint/key", json={"key": "   "})

    assert response.status_code == 400
    assert "not a credential" in response.json()["detail"]
    assert keychain.stored == {}


def test_setting_a_key_unparks_the_queue(vault, keychain):
    """A rotated key costs one failed job, not a restart. MODELS.md."""
    from agent.extract import jobs as jobs_mod

    queue = jobs_mod.Queue.open(vault.root / ".agent")
    with api_client(vault) as client:
        client.post("/api/capture", files={"files": ("a.jpg", JPEG, "image/jpeg")})
        queue = jobs_mod.Queue.open(vault.root / ".agent")
        queue.park_for_auth("the box rejected the key")
        assert jobs_mod.Queue.open(vault.root / ".agent").is_parked

        body = client.post("/api/settings/endpoint/key", json={"key": KEY}).json()

    assert body["resumed"] >= 1
    assert not jobs_mod.Queue.open(vault.root / ".agent").is_parked


# --- the model list --------------------------------------------------------


def test_the_model_list_comes_back_verbatim(client, monkeypatch):
    box(monkeypatch, models=[MODEL, "Qwen3.5-4B"])

    body = client.post("/api/settings/endpoint/models", json={"base_url": URL}).json()

    assert body["reached"] is True
    assert body["models"] == [MODEL, "Qwen3.5-4B"]


def test_a_sleeping_box_is_an_empty_list_and_a_sentence_not_an_error(client, monkeypatch):
    box(monkeypatch, refuse=True)

    response = client.post("/api/settings/endpoint/models", json={"base_url": URL})

    assert response.status_code == 200
    body = response.json()
    assert body["reached"] is False
    assert body["state"] == "unreachable"
    assert "keeps queuing" in body["message"]


def test_the_model_list_never_carries_what_the_box_said(client, monkeypatch):
    box(monkeypatch, status=401)

    body = client.post("/api/settings/endpoint/models", json={"base_url": URL}).json()

    assert body["state"] == "unauthorised"
    assert KEY not in json.dumps(body)
    assert "rejected token" not in json.dumps(body)


# --- the test button -------------------------------------------------------


def _steps(body) -> dict[str, str]:
    return {step["name"]: step["state"] for step in body["steps"]}


def test_a_working_box_passes_every_step_including_vision(client, monkeypatch):
    box(monkeypatch)

    body = client.post("/api/settings/endpoint/test", json=draft()).json()

    assert body["ok"] is True
    assert body["state"] == "working"
    assert _steps(body) == {
        "address": "ok",
        "credential": "ok",
        "reachable": "ok",
        "authentication": "ok",
        "model": "ok",
        "grammar": "ok",
        "vision": "ok",
    }


def test_every_step_is_listed_even_the_ones_never_reached(client, monkeypatch):
    """A list that shortened itself would hide where the test stopped."""
    box(monkeypatch, refuse=True)

    body = client.post("/api/settings/endpoint/test", json=draft()).json()

    assert [step["name"] for step in body["steps"]] == [
        name for name, _ in endpoint_check.STEPS
    ]


def test_a_sleeping_box_is_unreachable_and_nothing_else(client, monkeypatch):
    box(monkeypatch, refuse=True)

    body = client.post("/api/settings/endpoint/test", json=draft()).json()

    assert body["state"] == "unreachable"
    steps = _steps(body)
    assert steps["reachable"] == "failed"
    assert steps["authentication"] == "not-checked", "it was never asked"
    assert body["auth"] == "ok", "nothing rejected the key; nothing tried it"


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_is_unauthorised_and_the_box_still_answered(
    client, monkeypatch, status
):
    """The distinction the whole endpoint design turns on, on one screen."""
    box(monkeypatch, status=status)

    body = client.post("/api/settings/endpoint/test", json=draft()).json()

    assert body["state"] == "unauthorised"
    assert body["auth"] == "failed"
    steps = _steps(body)
    assert steps["reachable"] == "ok", "it answered — that is what makes this different"
    assert steps["authentication"] == "failed"
    assert steps["model"] == "not-checked"

    detail = next(s["detail"] for s in body["steps"] if s["name"] == "authentication")
    assert "refused the password" in detail
    assert "cannot lock you out" in detail


def test_a_box_that_throws_pictures_away_fails_its_own_step(client, monkeypatch):
    """The failure this button exists for: everything else looks perfect."""
    box(monkeypatch, sees_images=False)

    body = client.post("/api/settings/endpoint/test", json=draft()).json()

    assert body["ok"] is False
    assert body["state"] == "vision-not-working"
    steps = _steps(body)
    assert steps["grammar"] == "ok", "it answers text perfectly, which is the trap"
    assert steps["vision"] == "failed"

    detail = next(s["detail"] for s in body["steps"] if s["name"] == "vision")
    assert "mlx_lm.server" in detail
    assert "mmproj" in detail
    assert "looks like nothing being wrong" in detail


def test_a_box_that_ignores_the_schema_is_a_grammar_failure_not_a_vision_one(
    client, monkeypatch
):
    box(monkeypatch, honours_schema=False)

    body = client.post("/api/settings/endpoint/test", json=draft()).json()

    steps = _steps(body)
    assert steps["grammar"] == "failed"
    assert steps["vision"] == "not-checked"
    detail = next(s["detail"] for s in body["steps"] if s["name"] == "grammar")
    assert "guided-grammar" in detail


def test_a_box_running_something_else_fails_the_model_step(client, monkeypatch):
    box(monkeypatch, models=["some-other-model"])

    body = client.post("/api/settings/endpoint/test", json=draft()).json()

    steps = _steps(body)
    assert steps["authentication"] == "ok"
    assert steps["model"] == "failed"
    detail = next(s["detail"] for s in body["steps"] if s["name"] == "model")
    assert "answerable" in detail


def test_a_test_with_no_credential_stops_at_the_credential_step(
    client, monkeypatch, no_key
):
    box(monkeypatch)

    body = client.post("/api/settings/endpoint/test", json=draft()).json()

    steps = _steps(body)
    assert steps["credential"] == "failed"
    assert steps["reachable"] == "not-checked", "the key is never sent to an unchecked box"
    assert body["auth"] == "missing"


def test_the_test_result_carries_nothing_the_box_said(client, monkeypatch):
    """Fixed sentences, selected by code. Same rule as /api/health."""
    box(monkeypatch, status=401)

    body = client.post("/api/settings/endpoint/test", json=draft())

    assert KEY not in body.text
    assert "rejected token" not in body.text
    for length in range(12, len(KEY) + 1):
        assert KEY[:length] not in body.text


def test_testing_updates_what_every_other_screen_believes(client, monkeypatch):
    """Having just watched it pass, the banner must not still say it is asleep."""
    box(monkeypatch)

    client.post("/api/settings/endpoint/test", json=draft())

    health = client.get("/api/health").json()["endpoint"]
    assert health["state"] == "working"
    assert health["auth"] == "ok"
    assert health["model"] == MODEL


def test_testing_writes_nothing(client, vault, monkeypatch):
    """A test button that saved would make trying an address a change to undo."""
    box(monkeypatch)
    before = (vault.root / CONFIG_FILENAME).read_bytes()

    client.post("/api/settings/endpoint/test", json=draft(base_url="http://127.0.0.1:1/v1"))

    assert (vault.root / CONFIG_FILENAME).read_bytes() == before
    assert client.get("/api/settings").json()["endpoint"]["configured"] is False


# --- the write itself ------------------------------------------------------
#
# The same surgical rewrite as `sync_profile`, now aimed at a key inside a
# table. What is tested here is what a regex over hand-edited TOML gets wrong,
# because those failures are silent: the file loads, and means something else.


def test_a_second_copy_of_the_table_is_caught_by_reading_the_value_back(vault_root):
    """A file that loads is not a file that means what was written.

    TOML takes the *last* definition, so a rewrite that lands in the first of
    two ``[models.vlm]`` tables produces a perfectly valid file that still
    points at the old box. Only reading the value back out of the parsed
    document notices.
    """
    from agent import config as config_mod
    from agent.errors import ConfigError

    path = vault_root / CONFIG_FILENAME
    path.write_text(
        'sync_profile = "local"\n'
        "\n[models.vlm]\n"
        'base_url = "http://127.0.0.1:1/v1"\n'
        'model = "old"\n'
        "\n[models.asr]\nname = \"x\"\n",
        encoding="utf-8",
    )
    # A duplicate table is not legal TOML, so the file will not load at all —
    # which is the other way this fails, and it must also leave the original.
    original = path.read_bytes()

    def broken(text: str) -> str:
        return text + '\n[models.vlm]\nbase_url = "http://127.0.0.1:2/v1"\n'

    import pytest as _pytest

    monkey = _pytest.MonkeyPatch()
    monkey.setattr(config_mod, "_rewrite_values", lambda text, values: broken(text))
    try:
        with _pytest.raises(ConfigError):
            config_mod.set_values(path, {"models.vlm.base_url": URL})
    finally:
        monkey.undo()

    assert path.read_bytes() == original


def test_windows_line_endings_are_kept(vault_root):
    from agent import config as config_mod

    path = vault_root / CONFIG_FILENAME
    path.write_bytes(b'sync_profile = "local"\r\n\r\n[models.vlm]\r\nmodel = "old"\r\n')

    config_mod.set_values(path, {"models.vlm.model": MODEL})

    data = path.read_bytes()
    assert b"\r\n" in data
    # Every newline still has its carriage return: a rewrite that mixed the two
    # would make the whole file show as changed in a diff.
    assert data.count(b"\n") == data.count(b"\r\n")
    assert config_mod.load(path).raw["models"]["vlm"]["model"] == MODEL


def test_a_value_needing_escapes_survives_the_round_trip(vault_root):
    """Model ids carry slashes; a path or a name could carry worse."""
    from agent import config as config_mod

    path = vault_root / CONFIG_FILENAME
    path.write_text('sync_profile = "local"\n', encoding="utf-8")
    awkward = 'weird\\name "quoted" /slashed'

    config_mod.set_values(path, {"models.vlm.model": awkward})

    assert config_mod.load(path).raw["models"]["vlm"]["model"] == awkward
