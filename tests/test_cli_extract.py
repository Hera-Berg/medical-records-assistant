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

import argparse
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


# --- naming one artefact ---------------------------------------------------
#
# `--limit` says how many artefacts run and never which. Re-running one specific
# image is what checking a bad read actually consists of, so it needs its own
# flag.


def _two_artifacts(vault):
    """Two ingested images, returned as their short hashes in a stable order."""
    first = ingest_mod.ingest_bytes(
        vault, _image("PERINDOPRIL 5mg"), ingest_mod.CaptureContext(source="camera")
    )
    second = ingest_mod.ingest_bytes(
        vault, _image("METFORMIN 500mg"), ingest_mod.CaptureContext(source="camera")
    )
    return first.event.payload["short"], second.event.payload["short"]


def test_artifact_runs_only_the_one_named(monkeypatch, configured):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=_answer())

    first, _second = _two_artifacts(configured)
    _patch_client(monkeypatch, handler)

    code, output = _run(["extract", "--artifact", first], configured)

    assert code == 0
    assert f"selected  {first}" in output
    assert len(seen) == 1, "the other artefact was not sent"
    extractions = [
        e for e in configured.read().events if e.type == "extraction.completed"
    ]
    assert [e.payload["artifact"] for e in extractions] == [first]


def test_artifact_accepts_an_unambiguous_prefix(monkeypatch, configured):
    first, _ = _two_artifacts(configured)
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))

    code, output = _run(["extract", "--artifact", first[:4]], configured)

    assert code == 0
    assert f"selected  {first}" in output


def test_an_unknown_hash_is_a_message_not_an_empty_pass(monkeypatch, configured):
    _two_artifacts(configured)
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))

    code, output = _run(["extract", "--artifact", "zzzz99"], configured)

    assert code == 1
    assert "no artefact in this vault has the hash 'zzzz99'" in output
    assert "raw/ filename" in output, "says where to find a real one"


def test_an_ambiguous_prefix_names_the_candidates_rather_than_guessing(
    monkeypatch, configured
):
    """Re-running the wrong photograph and being told it read fine is worse
    than being asked to type two more characters.

    The images are fixed bytes, so their hashes are fixed too: enough of them
    and two share a first character every time this runs.
    """
    shorts = [
        ingest_mod.ingest_bytes(
            configured, _image(f"DRUG {n}"), ingest_mod.CaptureContext(source="camera")
        ).event.payload["short"]
        for n in range(12)
    ]
    collisions = {}
    for short in shorts:
        collisions.setdefault(short[0], []).append(short)
    shared, both = next(
        (prefix, found) for prefix, found in collisions.items() if len(found) > 1
    )
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))

    code, output = _run(["extract", "--artifact", shared], configured)

    assert code == 1
    assert f"matches {len(both)} artefacts" in output
    for short in both:
        assert short in output


def test_naming_an_artefact_re_opens_a_job_that_had_given_up(monkeypatch, configured):
    """The commonest reason to type a hash is that its job stopped being retried."""
    first, _ = _two_artifacts(configured)
    queue = jobs_mod.Queue.open(configured.root / ".agent")
    queue.update(queue.add(first), jobs_mod.NEEDS_ATTENTION, "gave up earlier")

    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))
    code, output = _run(["extract", "--artifact", first], configured)

    assert code == 0
    assert "1 claim proposed" in output


def test_naming_an_artefact_still_cannot_propose_its_claims_twice(
    monkeypatch, configured
):
    """Forceful about the queue, never about the log. Re-opening a job is a
    local decision; re-proposing claims would duplicate the record."""
    first, _ = _two_artifacts(configured)
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))
    _run(["extract", "--artifact", first], configured)

    code, output = _run(["extract", "--artifact", first], configured)

    assert code == 0
    assert "already read by" in output
    assert "Change the model or the prompt to re-derive it" in output
    proposed = [e for e in configured.read().events if e.type == "claim.proposed"]
    assert len(proposed) == 1


# --- a run that does nothing says why --------------------------------------
#
# "appended 0 events, 0 claims proposed" with no lines above it is
# indistinguishable from a broken command.


def test_an_empty_vault_says_there_is_nothing_to_read(monkeypatch, configured):
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))
    code, output = _run(["extract"], configured)

    assert code == 0
    assert "nothing   there is nothing to read" in output
    assert "health-agent ingest" in output


def test_a_fully_read_vault_says_so_rather_than_going_quiet(monkeypatch, configured):
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))
    _run(["extract"], configured)

    code, output = _run(["extract"], configured)

    assert code == 0
    assert "nothing   nothing to read: all 1 artefact(s)" in output
    assert "would propose the same claims a second time" in output


def test_an_idle_run_says_how_much_of_the_vault_was_already_read(
    monkeypatch, configured
):
    """The question underneath "why was this quiet" is "was my vault read"."""
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )
    # A recording: the vision model correctly declines it, so it stays unread
    # and terminal while the image is read.
    ingest_mod.ingest_bytes(
        configured,
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x80\x3e\x00\x00\x00\x7d\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00",
        ingest_mod.CaptureContext(source="recorder"),
    )
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))
    _run(["extract"], configured)

    _, output = _run(["extract"], configured)

    assert "1 of 2 artefact(s) have already been extracted" in output
    assert "not being retried (1 unreadable)" in output
    assert "--artifact <hash>` re-runs one" in output


def test_a_dry_run_that_would_run_nothing_says_why_too(monkeypatch, configured):
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))
    code, output = _run(["extract", "--dry-run"], configured)

    assert code == 0
    assert "dry run   0 job(s) would run" in output
    assert "nothing would run: there is nothing to read" in output


def test_the_idle_reason_reaches_the_json_too(monkeypatch, configured):
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))
    _, output = _run(["extract", "--json"], configured)

    assert "nothing to read" in json.loads(output)["idle_reason"]


# --- "0 claims proposed" said four different things ------------------------
#
# A page that states nothing clinical, an answer the validator refused, a server
# emitting thinking narration, and a photograph the model genuinely could not
# read are four outcomes needing four different actions. Only the third and
# fourth are anyone's fault, and they are not the same person's.


def _readable(claims):
    return json.dumps(
        {
            "artifact_kind": "prescription",
            "readable": True,
            "unreadable_reason": None,
            "document_date": None,
            "claims": claims,
        }
    )


def _extract_one(monkeypatch, vault, content):
    ingest_mod.ingest_bytes(vault, _image(), ingest_mod.CaptureContext(source="camera"))
    _patch_client(
        monkeypatch, lambda r: httpx.Response(200, json=_answer(content=content))
    )
    return _run(["extract"], vault)


def test_a_page_with_nothing_clinical_on_it_says_that(monkeypatch, configured):
    code, output = _extract_one(monkeypatch, configured, _readable([]))

    assert code == 0
    assert "nothing clinical found" in output
    assert "states nothing this record tracks" in output


def test_thinking_narration_is_reported_as_a_rejected_output(monkeypatch, configured):
    """The failure this distinction exists for. A reasoning trace in `content`
    is a server setting to change, and calling it an unreadable photograph sends
    the user to retake a photograph that was fine."""
    code, output = _extract_one(
        monkeypatch, configured, "<think>Let me look at the dose…</think> 5mg."
    )

    assert code == 0
    assert "output rejected:" in output
    assert "not JSON" in output
    assert "could not read it" not in output


def test_a_claim_the_validator_refused_is_reported_as_rejected(monkeypatch, configured):
    code, output = _extract_one(
        monkeypatch, configured, _readable([_claim(subject_name="   ")])
    )

    assert code == 0
    assert "output rejected: claim 0:" in output
    assert "does not resolve to a usable entity id" in output


def test_a_page_the_model_could_not_read_is_reported_as_that(monkeypatch, configured):
    code, output = _extract_one(
        monkeypatch,
        configured,
        json.dumps(
            {
                "artifact_kind": "other",
                "readable": False,
                "unreadable_reason": "the page is too blurred to make out any text",
                "document_date": None,
                "claims": [],
            }
        ),
    )

    assert code == 0
    assert "the model could not read it — review manually" in output
    assert "too blurred" in output
    assert "output rejected" not in output


def test_the_reading_is_carried_in_the_json_as_well_as_the_prose(
    monkeypatch, configured
):
    """Phase 5 reports queue state over HTTP and will need the same distinction."""
    _extract_one(monkeypatch, configured, _readable([]))
    read = [e for e in configured.read().events if e.type == "extraction.completed"]
    short = read[0].payload["artifact"]

    _, output = _run(["extract", "--json", "--artifact", short], configured)
    report = json.loads(output)

    assert report["outcomes"][0]["reading"] == "already-read"
    assert report["stats"]["by_reading"] == {"already-read": 1}


def test_a_read_line_never_runs_its_label_into_the_hash(monkeypatch, configured):
    """`unreadable3be48e` — the states are longer than the column they sit in."""
    ingest_mod.ingest_bytes(
        configured,
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x80\x3e\x00\x00\x00\x7d\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00",
        ingest_mod.CaptureContext(source="recorder"),
    )
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))
    _, output = _run(["extract"], configured)

    unreadable = [line for line in output.splitlines() if line.startswith("unreadable")]
    assert unreadable
    assert unreadable[0].startswith("unreadable ")


# --- --vault, in either position -------------------------------------------


def test_vault_is_accepted_after_the_subcommand(configured):
    """`health-agent extract --vault X` is the order people type, and it used to
    fail with argparse's bare "unrecognized arguments"."""
    out = io.StringIO()
    code = cli.main(["extract", "--vault", str(configured.root), "--dry-run"], out=out)

    assert code == 0
    assert "dry run" in out.getvalue()


def test_vault_before_the_subcommand_is_not_overwritten_by_the_default(configured):
    """`SUPPRESS` on the subparser is what makes accepting both safe: a plain
    default there would write None over the earlier position's value."""
    out = io.StringIO()
    code = cli.main(["--vault", str(configured.root), "extract", "--dry-run"], out=out)

    assert code == 0
    assert "dry run" in out.getvalue()


def test_the_later_position_wins_when_both_are_given(tmp_path, configured):
    parsed = cli.build_parser().parse_args(
        ["--vault", "first", "extract", "--vault", "second"]
    )
    assert parsed.vault == "second"


def test_every_subcommand_takes_vault_in_both_positions():
    """One missing subparser is a command that fails the way `extract` did."""
    parser = cli.build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    for name, sub in subparsers.choices.items():
        options = {option for action in sub._actions for option in action.option_strings}
        assert "--vault" in options, f"{name} does not accept --vault after itself"


def test_json_output_is_only_json(monkeypatch, configured):
    """A "queued 3" line above the object makes the whole output unparseable,
    which is the one thing --json exists to prevent. What those lines said is in
    the object instead."""
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=_answer()))

    _, output = _run(["extract", "--json"], configured)
    report = json.loads(output)

    assert output.lstrip().startswith("{")
    assert len(report["queued"]) == 1
    assert report["stats"]["claims"] == 1


def test_the_proposal_records_the_wording_the_source_used(monkeypatch, configured):
    """Salt variants are filed under the base drug by the projection, so the
    claim payload is the only place the label's own words survive in a form
    anything renders."""
    ingest_mod.ingest_bytes(
        configured, _image(), ingest_mod.CaptureContext(source="camera")
    )
    body = _answer(claims=[_claim(subject_name="Perindopril Arginine")])
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json=body))

    _run(["extract"], configured)
    proposed = next(
        e for e in configured.read().events if e.type == "claim.proposed"
    )

    assert proposed.payload["subject"] == "med:perindopril-arginine"
    assert proposed.payload["subject_name"] == "Perindopril Arginine"
