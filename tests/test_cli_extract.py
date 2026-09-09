"""The commands a person actually types, and what they are told.

Every message here is read by someone trying to get their own health record
working, often on a machine misbehaving in a way they did not cause. So these
tests assert on the wording as much as the exit code — particularly on the
distinction between "the box is asleep" and "your key was rejected", which is
the one that decides whether anybody investigates.

The box is an ``httpx.MockTransport`` throughout. ``session.open_client`` is
patched to hand it over, which is also what phase 5 will do.
"""

from __future__ import annotations

import io
import json

import httpx
import pytest
from PIL import Image

from agent import cli
from agent import config as config_mod
from agent import ingest as ingest_mod
from agent.extract import jobs as jobs_mod, probe as probe_mod, session
from agent.llm import credentials, redaction
from agent.llm.client import Client
from agent.llm.settings import parse as parse_settings

KEY = "sk-cli-1234567890abcdefghij"
MODEL = "Qwen3.8-Flash-Next-oQ4e-mtp"
ENV = "HEALTH_VLM_TOKEN_TEST"

CONFIG = """\
sync_profile = "local"
port = 7777
locale = "en-au"

[models.vlm]
base_url = "https://box.tailnet.ts.net/v1"
model = "Qwen3.8-Flash-Next-oQ4e-mtp"

[models.vlm.auth]
api_key_env = "HEALTH_VLM_TOKEN_TEST"
"""


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    monkeypatch.setattr(credentials, "_from_keychain", lambda: None)
    redaction.forget_all()
    yield
    redaction.forget_all()


@pytest.fixture
def configured(vault):
    (vault.root / config_mod.CONFIG_FILENAME).write_text(CONFIG, encoding="utf-8")
    return vault


def _image(text="PERINDOPRIL 5mg"):
    from PIL import ImageDraw

    image = Image.new("RGB", (800, 400), "white")
    ImageDraw.Draw(image).text((30, 30), text, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _answer(claims=None, model=MODEL, content=None):
    payload = json.dumps(
        {
            "artifact_kind": "prescription",
            "readable": True,
            "unreadable_reason": None,
            "document_date": {"value": "2026-06-04", "precision": "day", "uncertainty_days": 0},
            "claims": claims if claims is not None else [_claim()],
        }
    )
    return {
        "id": "c1",
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content or payload}}
        ],
    }


def _claim(**overrides):
    return {
        "subject_kind": "med",
        "subject_name": "Perindopril",
        "predicate": "dose",
        "value_literal": "5mg daily",
        "evidence_tier": "prescriber-issued",
        "occurred_at": {"value": "4 June 2026", "precision": "day", "uncertainty_days": 0},
        "occurred_span": None,
        "dispense": {
            "quantity": "30 tablets",
            "frequency": "one daily",
            "repeats": "no repeats",
            "dose_units": None,
        },
        "source_span": "PERINDOPRIL 5mg",
        "confidence": 0.9,
        **overrides,
    }


def _patch_client(monkeypatch, handler, resolver=lambda h, p=None: ["100.94.135.1"]):
    def opener(vault, transport=None, resolver_=None):
        return Client(
            parse_settings(vault.config.raw).vlm,
            vault_root=vault.root,
            transport=httpx.MockTransport(handler),
            resolver=resolver,
        )

    monkeypatch.setattr(session, "open_client", opener)
    monkeypatch.setattr(cli.session, "open_client", opener)


def _run(argv, vault):
    out = io.StringIO()
    code = cli.main(["--vault", str(vault.root)] + argv, out=out)
    return code, out.getvalue()


# --- probe -----------------------------------------------------------------


def _vision_handler(sees_image=True, model=MODEL):
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        body = json.loads(request.content)
        looks_like_probe = any(
            part.get("type") == "image_url"
            for message in body["messages"]
            for part in (message["content"] if isinstance(message["content"], list) else [])
        )
        answer = probe_mod.PROBE_TEXT if (sees_image and looks_like_probe) else "no image here"
        return httpx.Response(200, json=_answer(content=answer, model=model))

    return handler


def test_probe_reports_working_and_never_the_key(monkeypatch, configured):
    _patch_client(monkeypatch, _vision_handler())
    code, output = _run(["probe"], configured)

    assert code == 0
    assert "endpoint  working" in output
    assert "auth      ok" in output
    assert KEY not in output and KEY[:8] not in output


def test_probe_says_when_the_box_is_blind_and_what_that_means(monkeypatch, configured):
    """The failure that otherwise reads as the model being bad at OCR."""
    _patch_client(monkeypatch, _vision_handler(sees_image=False))
    code, output = _run(["probe"], configured)

    assert code == 1
    assert "vision-not-working" in output
    assert "mlx_lm.server` is text-only" in output
    assert "mmproj" in output


def test_probe_separates_unreachable_from_unauthorised(monkeypatch, configured):
    def asleep(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _patch_client(monkeypatch, asleep)
    code, output = _run(["probe"], configured)
    assert code == 1
    assert "endpoint  unreachable" in output

    _patch_client(monkeypatch, lambda r: httpx.Response(401))
    code, output = _run(["probe"], configured)
    assert code == 1
    assert "endpoint  unauthorised" in output
    assert "auth      failed" in output


def test_probe_refuses_a_public_endpoint_before_reading_the_key(monkeypatch, configured):
    _patch_client(
        monkeypatch, _vision_handler(), resolver=lambda h, p=None: ["203.0.113.7"]
    )
    code, output = _run(["probe"], configured)

    assert code == 1
    assert "misconfigured" in output
    assert "outside private address space" in output
    assert "auth      missing" in output, "the credential was never even resolved"


def test_probe_says_where_it_looked_for_a_credential(monkeypatch, configured):
    """`status` returns one word for /api/health. A person needs the sentence."""
    monkeypatch.delenv(ENV, raising=False)
    _patch_client(monkeypatch, _vision_handler())
    code, output = _run(["probe"], configured)

    assert code == 1
    assert "no credential found" in output
    assert ENV in output, "names the variable it checked"
    assert "the OS keychain under" in output
    assert "health-agent set-key" in output


def test_probe_says_so_when_the_credential_is_present_but_empty(monkeypatch, configured):
    monkeypatch.setenv(ENV, "")
    _patch_client(monkeypatch, _vision_handler())
    code, output = _run(["probe"], configured)

    assert code == 1
    assert "set but empty" in output
    assert "looks like a rotated key" in output


def test_probe_prints_the_two_things_it_cannot_check(monkeypatch, configured):
    """A gap that says so can be acted on; a silent one reads as a bug."""
    _patch_client(monkeypatch, _vision_handler())
    _, output = _run(["probe"], configured)

    assert "tailscale funnel" in output
    assert "deskew is not implemented" in output


def test_probe_json_carries_no_credential(monkeypatch, configured):
    _patch_client(monkeypatch, _vision_handler())
    _, output = _run(["probe", "--json"], configured)
    report = json.loads(output)

    assert report["auth"] == "ok"
    assert KEY not in output
    assert set(report) == {"state", "auth", "model_reported", "checks", "notes"}


def test_probe_catches_a_model_the_box_does_not_offer(monkeypatch, configured):
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "some-other-model"}]})
        return httpx.Response(200, json=_answer())

    _patch_client(monkeypatch, handler)
    code, output = _run(["probe"], configured)

    assert code == 1
    assert "copy the id verbatim from /v1/models" in output.lower()


# --- extract ---------------------------------------------------------------


def test_extract_queues_reads_and_says_nothing_reached_the_wiki(monkeypatch, configured):
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))

    code, output = _run(["extract"], configured)

    assert code == 0
    assert "queued    1 artefact(s) not yet read" in output
    assert "1 claims proposed" in output
    # The single most important sentence in this command's output: a proposal
    # is not a record entry, and high-consequence claims never auto-apply.
    assert "Nothing has reached the wiki" in output


def test_extract_reports_a_sleeping_box_without_alarming_anybody(monkeypatch, configured):
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )

    def asleep(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _patch_client(monkeypatch, asleep)
    code, output = _run(["extract"], configured)

    assert code == 0, "a sleeping box is not a failure of the command"
    assert "unreachable" in output
    assert "still waiting — run again when the box is up" in output
    assert "PARKED" not in output


def test_extract_parks_and_says_what_to_do_when_the_key_is_rejected(
    monkeypatch, configured
):
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )
    _patch_client(monkeypatch, lambda r: httpx.Response(401))

    code, output = _run(["extract"], configured)

    assert code == 1, "this one does need a person"
    assert "PARKED" in output
    assert "may have rotated" in output
    assert "health-agent extract --resume" in output
    assert KEY not in output


def test_resume_un_parks_the_queue(monkeypatch, configured):
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )
    _patch_client(monkeypatch, lambda r: httpx.Response(401))
    _run(["extract"], configured)

    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))
    code, output = _run(["extract", "--resume"], configured)

    assert code == 0
    assert "resumed   1 parked job(s)" in output
    assert "1 claims proposed" in output


def test_a_dry_run_contacts_nothing_and_appends_nothing(monkeypatch, configured):
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_answer())

    _patch_client(monkeypatch, handler)
    before = len(list(configured.read().events))
    code, output = _run(["extract", "--dry-run"], configured)

    assert code == 0
    assert calls == []
    assert "dry run   1 job(s) would run" in output
    assert len(list(configured.read().events)) == before


def test_extract_is_idempotent_from_the_command_line(monkeypatch, configured):
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))

    _run(["extract"], configured)
    _, second = _run(["extract"], configured)

    proposed = [
        event for event in configured.read().events if event.type == "claim.proposed"
    ]
    assert len(proposed) == 1
    assert "0 claims proposed" in second


def test_the_locale_from_config_decides_an_ambiguous_date(monkeypatch, configured):
    """en-au reads 04/06/2026 as 4 June. A US config would read 6 April."""
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )
    body = _answer(claims=[_claim(occurred_at={
        "value": "04/06/2026", "precision": "day", "uncertainty_days": 0
    })])
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=body))

    _run(["extract"], configured)

    claim = next(e for e in configured.read().events if e.type == "claim.proposed")
    assert claim.payload["occurred_at"]["value"] == "2026-06-04"


def test_extract_reports_a_torn_job_line_without_rewriting_it(monkeypatch, configured):
    queue = jobs_mod.Queue.open(configured.root / ".agent")
    queue.add("a3f91c")
    with queue.path.open("ab") as handle:
        handle.write(b'{"id": "torn", "artif')

    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))
    _, output = _run(["extract", "--dry-run"], configured)

    assert "never rewritten" in output
    assert b'{"id": "torn", "artif' in queue.path.read_bytes()


# --- set-key ---------------------------------------------------------------


def test_set_key_refuses_an_empty_value(monkeypatch, configured):
    monkeypatch.setitem(__import__("sys").modules, "keyring", _FakeKeyring())
    code, output = _run(["set-key", "--key", "   "], configured)

    assert code == 1
    assert "an empty key is not a credential" in output


def test_set_key_writes_to_the_keychain_not_the_vault(monkeypatch, configured):
    fake = _FakeKeyring()
    monkeypatch.setitem(__import__("sys").modules, "keyring", fake)

    code, output = _run(["set-key", "--key", KEY], configured)

    assert code == 0
    assert fake.stored == [(credentials.KEYRING_SERVICE, credentials.KEYRING_ACCOUNT, KEY)]
    assert KEY not in output
    for path in configured.root.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()


def test_set_key_without_keyring_names_the_two_alternatives(monkeypatch, configured):
    monkeypatch.setitem(__import__("sys").modules, "keyring", None)
    code, output = _run(["set-key", "--key", KEY], configured)

    assert code == 1
    assert "api_key_env" in output
    assert "0600 file" in output


class _FakeKeyring:
    def __init__(self):
        self.stored = []

    def set_password(self, service, account, value):
        self.stored.append((service, account, value))

    def get_password(self, service, account):
        return None


# --- config ----------------------------------------------------------------


def test_a_key_in_config_toml_is_still_a_startup_failure(vault):
    (vault.root / config_mod.CONFIG_FILENAME).write_text(
        CONFIG + '\napi_key = "sk-oops"\n', encoding="utf-8"
    )
    out = io.StringIO()
    code = cli.main(["--vault", str(vault.root), "check"], out=out)

    assert code == 1
    assert "already disclosed" in out.getvalue()


def test_the_template_config_parses_as_model_settings():
    """What first-run setup writes has to actually load."""
    import tomllib

    data = tomllib.loads(config_mod.CONFIG_TEMPLATE)
    settings = parse_settings(data)

    assert settings.vlm.model
    assert settings.vlm.auth.api_key_env == "HEALTH_VLM_TOKEN"
    assert config_mod.scan_for_secrets(data) == [], "and carries no secret"
