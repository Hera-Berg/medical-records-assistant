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


def test_the_sentence_in_front_of_the_fields_is_one_sentence(client):
    """Not a sign-up, a machine they already own — said once and said short.

    Three paragraphs stood here. A wall of reassurance in front of the first
    input is read by nobody, least of all the person who needs reassuring, so
    what remains is the two things that have to be established before anything
    is typed.
    """
    endpoint = client.get("/api/settings").json()["endpoint"]
    explanation = endpoint["explanation"]

    assert explanation.count(".") == 1, explanation
    assert "a computer you own" in explanation.lower()
    assert "nothing leaves your own network" in explanation.lower()


def test_the_rest_of_the_explanation_is_kept_one_tap_away(client):
    """Folded, not cut. The reader who wants it can still have all of it."""
    about = client.get("/api/settings").json()["endpoint"]["about"].lower()

    assert "refuses to start if you point it at one" in about
    # The sentence that stops a sleeping box reading as a broken record.
    assert "nothing breaks" in about


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
    """The prohibition and its reason. Not a claim about where it *is*.

    Where it is depends on which of three places answered, and the field says so
    on its own line. This paragraph used to open by asserting the keychain,
    which read as a contradiction directly under "Stored in the credentials
    file".
    """
    explanation = client.get("/api/settings").json()["endpoint"]["key_explanation"]

    assert explanation.startswith("A key never goes in your settings file")
    assert "Dropbox" in explanation
    assert "already been handed to a company" in explanation


def test_no_key_set_is_not_set_rather_than_an_error(client, no_key):
    key = client.get("/api/settings").json()["endpoint"]["key"]
    assert key == {"state": "not set", "source": None, "detail": None}


# --- connecting, which is the only way anything is saved -------------------


def connect(client, **overrides):
    return client.post("/api/settings/endpoint/connect", json=draft(**overrides))


def _steps(body) -> dict[str, str]:
    return {step["name"]: step["state"] for step in body["check"]["steps"]}


# --- the working path ------------------------------------------------------


def test_connecting_checks_the_box_and_then_writes_the_file(client, vault, monkeypatch):
    """One press: reach it, check it end to end, save it."""
    box(monkeypatch)

    body = connect(client, model=None).json()

    assert body["outcome"] == "connected"
    assert body["saved"] is True
    assert body["status"] == f"Connected — reading with {MODEL}."

    text = (vault.root / CONFIG_FILENAME).read_text(encoding="utf-8")
    assert f'base_url = "{URL}"' in text
    assert f'model = "{MODEL}"' in text
    assert client.get("/api/settings").json()["endpoint"]["base_url"] == URL


def test_the_status_line_is_the_whole_of_what_a_person_has_to_read(client, monkeypatch):
    """Everything else folds away. The sentence has to stand on its own."""
    box(monkeypatch)

    body = connect(client, model=None).json()

    assert MODEL in body["status"], "it names what is actually reading the documents"
    assert body["check"]["steps"], "and the steps are there for whoever opens them"


def test_the_model_is_taken_from_the_box_and_stored_verbatim(client, vault, monkeypatch):
    """``Jundot/Qwen…`` is not ``Qwen…``, and identity is checked on every call.

    Nobody types it. That is the point of reaching the box before asking: the
    one string that has to match exactly is the one string a person is most
    likely to get subtly wrong.
    """
    box(monkeypatch)

    body = connect(client, model=None).json()

    assert body["model"] == MODEL
    from agent.extract import session

    assert session.settings_for(vault).vlm.model == MODEL
    assert "Jundot/" in (vault.root / CONFIG_FILENAME).read_text(encoding="utf-8")


def test_the_comments_in_the_settings_file_survive_the_write(client, vault, monkeypatch):
    """The reason this is a line rewrite rather than a TOML round-trip."""
    from agent import config as config_mod

    path = vault.root / CONFIG_FILENAME
    path.write_text(config_mod.CONFIG_TEMPLATE, encoding="utf-8")
    client.app.state.record.reload_config()
    box(monkeypatch)

    connect(client, model=None)
    text = path.read_text(encoding="utf-8")

    assert "Never put a key, token or" in text
    assert "# must resolve to 100.x / RFC1918" in text
    assert "# copy verbatim from /v1/models" in text
    assert "max_pixels = 1638400" in text, "and every value not on the screen"


def test_an_empty_scheme_is_a_real_answer_and_reaches_the_header(
    client, vault, monkeypatch
):
    """Some self-hosted servers want ``X-API-Key: <key>`` with no word in front."""
    box(monkeypatch)

    connect(client, model=None, header="X-API-Key", scheme="")

    auth = client.get("/api/settings").json()["endpoint"]["auth"]
    assert auth == {"header": "X-API-Key", "scheme": "", "api_key_env": None}

    from agent.extract import session

    assert session.settings_for(vault).vlm.auth.format("abc") == ("X-API-Key", "abc")


def test_connecting_updates_what_every_other_screen_believes(client, monkeypatch):
    """Having just watched it connect, the banner must not still say it is asleep."""
    box(monkeypatch)

    connect(client, model=None)

    health = client.get("/api/health").json()["endpoint"]
    assert health["state"] == "working"
    assert health["auth"] == "ok"
    assert health["model"] == MODEL


# --- choosing, which is a question and not a failure -----------------------


def test_two_models_is_a_question_rather_than_a_failure(client, vault, monkeypatch):
    """The list cannot be offered before the box has been reached."""
    box(monkeypatch, models=[MODEL, "Qwen3.5-4B"])

    body = connect(client, model=None).json()

    assert body["outcome"] == "choose-model"
    assert body["saved"] is False
    assert body["models"] == [MODEL, "Qwen3.5-4B"], "verbatim, in the box's own order"
    assert "choose which one" in body["status"]
    # Nothing was written: a question is not a configuration.
    assert client.get("/api/settings").json()["endpoint"]["configured"] is False


def test_the_answer_to_that_question_connects(client, monkeypatch):
    box(monkeypatch, models=[MODEL, "Qwen3.5-4B"])
    assert connect(client, model=None).json()["outcome"] == "choose-model"

    body = connect(client, model=MODEL).json()

    assert body["outcome"] == "connected"
    assert body["saved"] is True


def test_one_model_is_not_a_question(client, monkeypatch):
    """Asking which of one would be ceremony. The id is still recorded verbatim."""
    box(monkeypatch, models=[MODEL])

    body = connect(client, model=None).json()

    assert body["outcome"] == "connected"
    assert body["model"] == MODEL


def test_a_box_that_lists_nothing_says_to_type_the_name(client, monkeypatch):
    box(monkeypatch, models=[])

    body = connect(client, model=None).json()

    assert body["outcome"] == "choose-model"
    assert body["models"] == []
    assert "Type the name in yourself" in body["status"]


# --- one failure surface ---------------------------------------------------


def test_a_failure_is_one_sentence_with_the_rest_behind_it(client, monkeypatch):
    """A headline, a detail, and the steps. Not three competing red boxes."""
    box(monkeypatch, refuse=True)

    body = connect(client, model=None).json()

    assert body["outcome"] == "failed"
    assert body["status"] == "That computer did not answer."
    assert "probably asleep" in body["detail"]
    assert body["check"] is not None


def test_nothing_is_written_when_it_does_not_work(client, vault, monkeypatch):
    """Saving a configuration known not to work has no value."""
    before = (vault.root / CONFIG_FILENAME).read_bytes()
    box(monkeypatch, refuse=True)

    body = connect(client, model=MODEL).json()

    assert body["saved"] is False
    assert (vault.root / CONFIG_FILENAME).read_bytes() == before
    assert client.get("/api/settings").json()["endpoint"]["configured"] is False


def test_a_public_address_is_refused_in_its_own_words(client, vault, monkeypatch):
    """The one refusal allowed to speak for itself: nothing has been sent yet.

    It is composed from the address that was typed and this machine's own
    resolver, at a moment when no request has gone anywhere and there is no
    far-end text in existence to leak.
    """
    box(monkeypatch)
    before = (vault.root / CONFIG_FILENAME).read_bytes()

    body = connect(client, base_url="https://93.184.216.34/v1", model=MODEL).json()

    assert body["outcome"] == "failed"
    assert body["status"] == "That address is not on your own network."
    assert "outside private address space" in body["detail"]
    assert "fork the project" in body["detail"]
    assert (vault.root / CONFIG_FILENAME).read_bytes() == before


def test_a_key_pasted_into_the_address_is_refused_and_named_as_disclosed(
    client, monkeypatch
):
    box(monkeypatch)

    body = connect(
        client, base_url="https://me:hunter2@127.0.0.1/v1", model=MODEL
    ).json()

    assert body["outcome"] == "failed"
    assert "username or password" in body["detail"]
    assert "syncs to Dropbox" in body["detail"]


def test_an_empty_address_is_the_one_thing_asked_for_by_name(client):
    response = client.post("/api/settings/endpoint/connect", json={"base_url": "  "})

    assert response.status_code == 400
    assert "ending in /v1" in response.json()["detail"]


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_is_its_own_sentence(client, monkeypatch, status):
    """The distinction the whole endpoint design turns on, in one line."""
    box(monkeypatch, status=status)

    body = connect(client, model=MODEL).json()

    assert body["status"] == "That computer refused the password."
    assert body["check"]["auth"] == "failed"
    assert body["check"]["state"] == "unauthorised"


def test_a_sleeping_box_and_a_rejected_key_never_read_the_same(client, monkeypatch):
    box(monkeypatch, refuse=True)
    asleep = connect(client, model=MODEL).json()
    box(monkeypatch, status=401)
    refused = connect(client, model=MODEL).json()

    assert asleep["status"] != refused["status"]
    assert asleep["check"]["state"] == "unreachable"
    assert refused["check"]["state"] == "unauthorised"


def test_a_box_that_throws_pictures_away_gets_its_own_sentence(client, monkeypatch):
    """The failure this exists for: everything else looks perfect."""
    box(monkeypatch, sees_images=False)

    body = connect(client, model=MODEL).json()

    assert body["outcome"] == "failed"
    assert body["status"] == "That computer cannot read words out of a picture."
    assert body["saved"] is False, "a blind box is not a working one"
    steps = _steps(body)
    assert steps["grammar"] == "ok", "it answers text perfectly, which is the trap"
    assert steps["vision"] == "failed"
    assert "mlx_lm.server" in body["detail"]
    assert "mmproj" in body["detail"]


def test_a_box_that_ignores_the_schema_is_a_grammar_failure_not_a_vision_one(
    client, monkeypatch
):
    box(monkeypatch, honours_schema=False)

    body = connect(client, model=MODEL).json()

    assert body["status"] == "That computer does not answer in the shape the record needs."
    steps = _steps(body)
    assert steps["grammar"] == "failed"
    assert steps["vision"] == "not-checked"


def test_a_box_running_something_else_fails_the_model_step(client, monkeypatch):
    """Asked for one model by name, offered another. Never quietly accepted."""
    box(monkeypatch, models=["some-other-model"])

    body = connect(client, model=MODEL).json()

    assert body["status"] == "That computer is running a different model."
    assert _steps(body)["model"] == "failed"


def test_no_credential_stops_before_anything_is_sent(client, monkeypatch, no_key):
    box(monkeypatch)

    body = connect(client, model=MODEL).json()

    assert body["status"] == "No password is stored for that computer."
    assert "health-agent set-key" in body["detail"], "its own words: nothing was sent"
    assert _steps(body)["reachable"] == "not-checked"


def test_every_step_is_listed_even_the_ones_never_reached(client, monkeypatch):
    """A list that shortened itself would hide where it stopped."""
    box(monkeypatch, refuse=True)

    body = connect(client, model=MODEL).json()

    assert [step["name"] for step in body["check"]["steps"]] == [
        name for name, _ in endpoint_check.STEPS
    ]


def test_a_failure_carries_nothing_the_box_said(client, monkeypatch):
    """Fixed sentences, selected by code. Same rule as /api/health."""
    box(monkeypatch, status=401)

    response = connect(client, model=MODEL)

    assert KEY not in response.text
    assert "rejected token" not in response.text
    for length in range(12, len(KEY) + 1):
        assert KEY[:length] not in response.text


def test_a_forked_settings_file_refuses_the_write(client, vault, monkeypatch):
    (vault.root / "config (1).toml").write_text("", encoding="utf-8")
    before = (vault.root / CONFIG_FILENAME).read_bytes()
    box(monkeypatch)

    response = connect(client, model=MODEL)

    assert response.status_code == 409
    assert "config (1).toml" in response.json()["detail"]
    assert (vault.root / CONFIG_FILENAME).read_bytes() == before


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


# --- a table that is there but will not load -------------------------------


def test_an_unusable_endpoint_table_is_reported_rather_than_shown_as_empty(
    client, vault
):
    """Empty fields would read as "not set up yet" when the truth is "set up wrongly".

    Reachable while the server runs: the file is re-read after every save, and a
    hand edit between two saves lands here.
    """
    from agent import config as config_mod

    (vault.root / CONFIG_FILENAME).write_text(
        'sync_profile = "local"\n'
        "\n[models.vlm]\n"
        f'base_url = "{URL}"\n'
        'model = "m"\n'
        "ctx = 99999999\n",
        encoding="utf-8",
    )
    client.app.state.record.reload_config()

    endpoint = client.get("/api/settings").json()["endpoint"]

    assert endpoint["problem"] is not None
    assert "ctx" in endpoint["problem"]
    assert endpoint["configured"] is False, "and the fields stay empty rather than lying"
    assert config_mod.load(vault.root / CONFIG_FILENAME).sync_profile.value == "local"


def test_connecting_over_an_unusable_table_writes_what_it_owns_and_still_reports(
    client, vault, monkeypatch
):
    """``ctx`` is not on this screen, so connecting cannot repair it.

    What it must not do is report a clean success that changed nothing a person
    can see: the four keys it owns are written, and the one it does not is still
    named.
    """
    (vault.root / CONFIG_FILENAME).write_text(
        'sync_profile = "local"\n'
        "\n[models.vlm]\n"
        f'base_url = "{URL}"\n'
        'model = "m"\n'
        "ctx = 99999999\n",
        encoding="utf-8",
    )
    client.app.state.record.reload_config()
    box(monkeypatch)

    body = connect(client, model=MODEL).json()

    assert body["settings"]["endpoint"]["problem"] is not None
    text = (vault.root / CONFIG_FILENAME).read_text(encoding="utf-8")
    assert URL in text and MODEL in text, "and what this screen does own was written"


# --- the worker ------------------------------------------------------------


def test_changing_the_endpoint_makes_the_worker_ask_again(vault):
    """A stall means "stop asking until a person acts". A person just acted."""
    from agent.server.state import RecordState
    from agent.server.worker import Worker

    worker = Worker(RecordState(vault))
    worker._stalled = True
    worker._probed = True

    worker.reconfigured()

    assert worker._stalled is False
    assert worker._probed is False, "what it learned was about the other machine"


def test_changing_the_endpoint_does_not_unpark_jobs(vault):
    """A new address is not a new key.

    Un-parking everything because a URL typo was corrected would send every job
    that stopped on a rejected credential straight back at a box that will
    reject them again — and MODELS.md is explicit that a rejected key is not
    retried.
    """
    from agent.extract import jobs as jobs_mod
    from agent.server.state import RecordState
    from agent.server.worker import Worker

    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add("a3f91c")
    queue.park_for_auth("the box rejected the key")
    assert jobs_mod.Queue.open(vault.root / ".agent").is_parked

    Worker(RecordState(vault)).reconfigured()

    assert jobs_mod.Queue.open(vault.root / ".agent").is_parked
