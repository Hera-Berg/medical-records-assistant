"""The whole pipeline: artefact in, events out, and what happens when it breaks.

Four of the tests ``MODELS.md`` names by hand live here —
``test_capture_survives_offline_endpoint``, ``test_retry_is_idempotent``,
``test_auth_failure_parks_queue`` and ``test_key_never_written_to_vault`` —
because none of them can exist without a real vault, a real job file and a real
event log.

Nothing touches the network. The model is an ``httpx.MockTransport`` and the
endpoint resolver is a function, so a sleeping box is something these tests
arrange deliberately rather than something that makes them flaky.
"""

from __future__ import annotations

import io
import json

import httpx

from . import family_answers
import pytest
from PIL import Image

from agent import ingest as ingest_mod
from agent.extract import jobs as jobs_mod
from agent.extract import propose, runner
from agent.extract.runner import Extractor
from agent.llm import credentials, redaction
from agent.llm.client import Client
from agent.llm.endpoint import parse as parse_endpoint
from agent.llm.settings import AuthSettings, VlmSettings

KEY = "sk-vault-a1b2c3d4e5f6a7b8c9d0"
MODEL = "Qwen3.8-Flash-Next-oQ4e-mtp"
ENV = "HEALTH_VLM_TOKEN_TEST"
PRIVATE = ["100.94.135.1"]


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    monkeypatch.setattr(credentials, "_from_keychain", lambda: None)
    redaction.forget_all()
    yield
    redaction.forget_all()


def _script_image(text_lines=("PERINDOPRIL 5mg", "One daily", "30 tablets")) -> bytes:
    """A synthetic script. Never real patient data — see CLAUDE.md, Testing."""
    image = Image.new("RGB", (1000, 700), "white")
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    for index, line in enumerate(text_lines):
        draw.text((40, 40 + index * 40), line, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _answer_body(claims=None, readable=True, model=MODEL, **overrides):
    payload = {
        "artifact_kind": "prescription",
        "readable": readable,
        "unreadable_reason": None,
        "document_date": {
            "value": "2026-06-04",
            "precision": "day",
            "uncertainty_days": 0,
        },
        "claims": claims if claims is not None else [_model_claim()],
        **overrides,
    }
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": json.dumps(payload)}}
        ],
        "usage": {"prompt_tokens": 900},
    }


def _model_claim(**overrides):
    base = {
        "subject_kind": "med",
        "subject_name": "Perindopril",
        "predicate": "dose",
        "value_literal": "5mg daily",
        "evidence_tier": "prescriber-issued",
        "occurred_at": {"value": "2026-06-04", "precision": "day", "uncertainty_days": 0},
        "occurred_span": None,
        "dispense": {
            "quantity": "30 tablets",
            "frequency": "one daily",
            "repeats": "no repeats",
            "dose_units": None,
        },
        "source_span": "PERINDOPRIL 5mg one daily",
        "confidence": 0.91,
    }
    return {**base, **overrides}


def _ingest(vault, data=None, filename="script.jpg"):
    result = ingest_mod.ingest_bytes(
        vault,
        data if data is not None else _script_image(),
        ingest_mod.CaptureContext(source="camera", original_filename=filename),
    )
    return result.short


def _client(vault, handler, resolver=lambda h, p=None: list(PRIVATE)):
    settings = VlmSettings(
        endpoint=parse_endpoint("https://box.tailnet.ts.net/v1"),
        model=MODEL,
        auth=AuthSettings(api_key_env=ENV),
    )
    return Client(
        settings,
        vault_root=vault.root,
        transport=httpx.MockTransport(family_answers.wrap(handler)),
        resolver=resolver,
    )


def _ok(body=None):
    def handler(request):
        return httpx.Response(200, json=body or _answer_body())

    return handler


# --- the happy path --------------------------------------------------------


def test_an_artefact_becomes_an_extraction_event_and_a_claim(vault):
    short = _ingest(vault)
    with _client(vault, _ok()) as client:
        outcome = Extractor(vault, client).run(short)

    kinds = [event.type for event in outcome.events]
    assert kinds.count(propose.EXTRACTION_COMPLETED) == 1
    assert kinds.count(propose.CLAIM_PROPOSED) == 1
    assert propose.MODEL_OBSERVED in kinds, "first sight of this model identity"


def test_the_raw_answer_is_stored_verbatim(vault):
    """A model swap is diffed against this; a parse is a lossy view of it."""
    short = _ingest(vault)
    with _client(vault, _ok()) as client:
        outcome = Extractor(vault, client).run(short)

    completed = next(e for e in outcome.events if e.type == propose.EXTRACTION_COMPLETED)
    turns = completed.payload["turns"]
    assert [turn["family"] for turn in turns] == ["medications", "allergies", "problems"]
    first = json.loads(turns[0]["raw_output"])
    assert first["medications"][0]["strength"] == "5mg"
    assert first["medications"][0]["frequency"] == "daily"
    # Every turn verbatim, and raw_output is the first of them — never a later
    # turn standing in for the whole answer.
    assert completed.payload["raw_output"] == turns[0]["raw_output"]
    assert all(turn["finish_reason"] is None or turn["finish_reason"] for turn in turns)
    assert completed.payload["sampling"]["temperature"] == 0.0
    assert completed.payload["prompt_hashes"][0].startswith("sha256:")
    assert completed.payload["images"][0]["vision_tokens_estimate"] > 0


def test_the_claim_carries_all_four_timestamps_and_invents_none(vault):
    short = _ingest(vault)
    with _client(vault, _ok()) as client:
        outcome = Extractor(vault, client).run(short)

    claim = next(e for e in outcome.events if e.type == propose.CLAIM_PROPOSED)
    payload = claim.payload

    assert payload["ingested_ts"], "always known — the bytes entered the vault"
    # An ingest from bytes with no live camera knows no capture time, and the
    # ingest time is emphatically not it.
    assert payload["captured_ts"] is None
    assert payload["artifact_ts"] == "2026-06-04T00:00:00Z", "the model read the date"
    assert payload["occurred_at"]["value"] == "2026-06-04"
    assert payload["ingested_ts"] != payload["artifact_ts"]


def test_a_month_precise_document_date_does_not_become_a_timestamp(vault):
    """Writing 2026-06-15T00:00:00Z would fake a precision the page never had."""
    short = _ingest(vault)
    body = _answer_body()
    content = json.loads(body["choices"][0]["message"]["content"])
    content["document_date"] = {
        "value": "June 2026",
        "precision": "month",
        "uncertainty_days": 0,
    }
    body["choices"][0]["message"]["content"] = json.dumps(content)

    with _client(vault, _ok(body)) as client:
        outcome = Extractor(vault, client).run(short)

    claim = next(e for e in outcome.events if e.type == propose.CLAIM_PROPOSED)
    assert claim.payload["artifact_ts"] is None

    completed = next(e for e in outcome.events if e.type == propose.EXTRACTION_COMPLETED)
    assert completed.payload["document_date"]["precision"] == "month", "still recorded"


def test_the_proposed_claim_reaches_the_wiki_only_after_a_tap(vault):
    """End to end against the phase 3 gate: high consequence never auto-applies.

    A dose is high consequence, so an unconfirmed proposal produces no
    medication page at all — not a page with an empty dose. It waits in the
    review queue instead.
    """
    from agent import projection

    short = _ingest(vault)
    with _client(vault, _ok()) as client:
        outcome = Extractor(vault, client).run(short)
    for event in outcome.events:
        vault.append(event)

    result = projection.project(vault.read().events, "2026-06-10T00:00:00Z")

    assert "wiki/medications/perindopril.md" not in result.files
    pending = [item for item in result.review if item.kind == "awaiting-confirmation"]
    assert [item.subject_id for item in pending] == ["med:perindopril"]
    assert pending[0].consequence == "high"


def test_a_confirmed_proposal_reaches_the_wiki_with_its_citation(vault):
    """The other half: once the user taps, the extracted claim renders."""
    from agent import projection
    from agent.demo.authoring import confirm

    short = _ingest(vault)
    with _client(vault, _ok()) as client:
        outcome = Extractor(vault, client).run(short)
    for event in outcome.events:
        vault.append(event)
    proposed = next(e for e in outcome.events if e.type == propose.CLAIM_PROPOSED)
    vault.append(confirm(vault.identity.id, proposed.id))

    result = projection.project(vault.read().events, "2026-06-10T00:00:00Z")
    page = result.files["wiki/medications/perindopril.md"].decode("utf-8")

    assert "dose: 5mg daily" in page, "the literal the model copied, not a normalised form"
    assert "document dated 4 June 2026" in page
    assert f"[^{short}]" in page, "and it cites the photograph it was read from"


# --- idempotency -----------------------------------------------------------


def test_retry_is_idempotent(vault):
    """MODELS.md names this one: three attempts, one claim.proposed per claim."""
    short = _ingest(vault)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_answer_body())

    for _ in range(3):
        with _client(vault, handler) as client:
            outcome = Extractor(vault, client).run(short)
        for event in outcome.events:
            vault.append(event)

    events = list(vault.read().events)
    assert sum(1 for e in events if e.type == propose.CLAIM_PROPOSED) == 1
    assert sum(1 for e in events if e.type == propose.EXTRACTION_COMPLETED) == 1
    assert len(calls) == 3, "one reading — three turns — and the model is not asked again"


def test_idempotency_survives_a_restart(vault):
    """The key is checked against the log, which is the thing that survives."""
    short = _ingest(vault)
    with _client(vault, _ok()) as client:
        for event in Extractor(vault, client).run(short).events:
            vault.append(event)

    # A "restart": a brand new extractor, client and queue over the same vault.
    with _client(vault, _ok()) as client:
        second = Extractor(vault, client).run(short)

    assert second.events == ()
    assert second.state == jobs_mod.DONE
    assert "already read" in second.reason


def test_a_model_that_produced_no_claims_is_not_re_read_forever(vault):
    """A page stating nothing is a settled answer, not work still to do."""
    short = _ingest(vault)
    with _client(vault, _ok(_answer_body(claims=[]))) as client:
        for event in Extractor(vault, client).run(short).events:
            vault.append(event)

    with _client(vault, _ok()) as client:
        assert Extractor(vault, client).run(short).events == ()


def test_the_model_identity_is_recorded_once_not_per_extraction(vault):
    first = _ingest(vault)
    second = _ingest(vault, _script_image(("ATORVASTATIN 20mg", "nocte")))

    with _client(vault, _ok()) as client:
        extractor = Extractor(vault, client)
        for short in (first, second):
            for event in extractor.run(short).events:
                vault.append(event)

    observed = [e for e in vault.read().events if e.type == propose.MODEL_OBSERVED]
    assert len(observed) == 1
    assert observed[0].payload["model"] == MODEL


# --- the endpoint being unavailable ---------------------------------------


def test_capture_survives_offline_endpoint(vault, tmp_path):
    """MODELS.md names this one.

    With the endpoint refused at the socket level: capture succeeds, the
    artefact lands in raw/, the job persists, and it drains on reconnect.
    """
    def refused(request):
        raise httpx.ConnectError("Connection refused", request=request)

    short = _ingest(vault)
    stored = vault.raw.find(
        next(
            e.payload["hash"]
            for e in vault.read().events
            if e.type == "artifact.ingested"
        )
    )
    assert stored is not None and stored.exists(), "the bytes are in raw/ regardless"

    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(short)

    with _client(vault, refused) as client:
        report = runner.drain(vault, Extractor(vault, client), queue)

    assert report.appended == 0
    assert not report.is_parked, "a sleeping box is not an auth problem"
    assert queue.for_artifact(short).state == jobs_mod.UNREACHABLE

    # The job survives a restart of the process.
    reopened = jobs_mod.Queue.open(vault.root / ".agent")
    assert reopened.for_artifact(short).state == jobs_mod.UNREACHABLE

    # And drains when the box is back.
    job = reopened.for_artifact(short)
    reopened.update(job, jobs_mod.QUEUED, "the box is back")
    with _client(vault, _ok()) as client:
        second = runner.drain(vault, Extractor(vault, client), reopened)

    assert second.appended > 0
    assert reopened.for_artifact(short).state == jobs_mod.DONE


def test_a_failed_job_backs_off_before_it_is_tried_again(vault):
    def refused(request):
        raise httpx.ConnectError("Connection refused", request=request)

    short = _ingest(vault)
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(short)

    with _client(vault, refused) as client:
        runner.drain(vault, Extractor(vault, client), queue)

    job = queue.for_artifact(short)
    assert job.not_before, "a backoff was set"
    assert queue.ready() == (), "and it is not immediately ready again"


def test_a_job_gives_up_rather_than_retrying_forever(vault):
    short = _ingest(vault)
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    job = queue.add(short)

    for _ in range(jobs_mod.MAX_ATTEMPTS):
        job = queue.started(job)
    job = queue.failed(job, "the box never came back")

    assert job.state == jobs_mod.NEEDS_ATTENTION
    assert "gave up after" in job.reason


# --- authentication --------------------------------------------------------


def test_auth_failure_parks_queue(vault):
    """MODELS.md names this one: 401 marks jobs blocked-auth and does not retry."""
    calls = []

    def rejects(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "invalid key"})

    first = _ingest(vault)
    second = _ingest(vault, _script_image(("ATORVASTATIN 20mg",)))
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(first)
    queue.add(second)

    with _client(vault, rejects) as client:
        report = runner.drain(vault, Extractor(vault, client), queue)

    assert report.is_parked
    assert len(calls) == 1, "the second job is not attempted; the key is the problem"
    assert {job.state for job in queue.blocked()} == {jobs_mod.BLOCKED_AUTH}
    assert len(queue.blocked()) == 2, "the whole queue parks, not just the job that failed"
    assert "authentication rejected by the inference box" in report.parked_reason
    assert "may have rotated" in report.parked_reason


def test_a_parked_queue_reports_rather_than_silently_doing_nothing(vault):
    short = _ingest(vault)
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(short)
    queue.park_for_auth("the key was rejected")

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_answer_body())

    with _client(vault, handler) as client:
        report = runner.drain(vault, Extractor(vault, client), queue)

    assert calls == []
    assert report.is_parked
    assert "health-agent extract --resume" in report.parked_reason


def test_resuming_puts_parked_jobs_back_in_the_queue(vault):
    short = _ingest(vault)
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(short)
    queue.park_for_auth("the key was rejected")

    resumed = queue.resume()

    assert len(resumed) == 1
    assert queue.for_artifact(short).state == jobs_mod.QUEUED
    assert queue.for_artifact(short).attempts == 0, "a fresh start, not a resumed backoff"
    assert not queue.is_parked


def test_unreachable_and_unauthorised_are_never_the_same_state(vault):
    """Collapsing them is how a rotated key looks like a sleeping Mac."""
    assert jobs_mod.UNREACHABLE != jobs_mod.BLOCKED_AUTH
    assert jobs_mod.UNREACHABLE in jobs_mod.RUNNABLE, "retried silently"
    assert jobs_mod.BLOCKED_AUTH in jobs_mod.TERMINAL, "needs a person"


# --- the key reaches nothing in the vault ----------------------------------


def test_key_never_written_to_vault(vault):
    """MODELS.md names this one: walk the whole vault and find no key anywhere."""
    short = _ingest(vault)

    def sometimes_fails(request):
        if not getattr(sometimes_fails, "failed", False):
            sometimes_fails.failed = True
            raise httpx.ConnectError(
                f"failed with Authorization: Bearer {KEY}", request=request
            )
        return httpx.Response(200, json=_answer_body())

    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(short)
    with _client(vault, sometimes_fails) as client:
        extractor = Extractor(vault, client)
        runner.drain(vault, extractor, queue)
        job = queue.for_artifact(short)
        queue.update(job, jobs_mod.QUEUED, "retry")
        runner.drain(vault, extractor, queue)

    checked = []
    for path in vault.root.rglob("*"):
        if not path.is_file():
            continue
        checked.append(path.relative_to(vault.root).as_posix())
        blob = path.read_bytes()
        assert KEY.encode() not in blob, f"the key reached {path}"
        assert KEY[:10].encode() not in blob, f"a prefix of the key reached {path}"

    # The walk is only reassuring if it covered the files that could plausibly
    # have carried the key: the job file records failure reasons, the event log
    # records provenance, and config.toml is the one the whole rule is about.
    assert "config.toml" in checked
    assert ".agent/jobs.jsonl" in checked
    assert any(name.startswith("events/") for name in checked)
    assert any(name.startswith("raw/") for name in checked)


def test_the_job_file_records_a_scrubbed_failure_reason(vault):
    def leaks(request):
        raise httpx.ConnectError(f"headers: Bearer {KEY}", request=request)

    short = _ingest(vault)
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(short)
    with _client(vault, leaks) as client:
        runner.drain(vault, Extractor(vault, client), queue)

    reason = queue.for_artifact(short).reason
    assert KEY not in reason
    assert redaction.REDACTED in reason


def test_credentials_never_reach_a_report(vault):
    """The phase-4 form of test_credentials_never_reach_frontend."""
    short = _ingest(vault)
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(short)
    with _client(vault, _ok()) as client:
        report = runner.drain(vault, Extractor(vault, client), queue)

    rendered = json.dumps(report.stats()) + " ".join(
        outcome.describe() for outcome in report.outcomes
    )
    assert KEY not in rendered
    assert KEY[:8] not in rendered


# --- unreadable artefacts --------------------------------------------------


def test_an_unreadable_page_is_reviewed_by_a_person_not_retried(vault):
    short = _ingest(vault)
    body = _answer_body(claims=[], readable=False)
    content = json.loads(body["choices"][0]["message"]["content"])
    content["unreadable_reason"] = "the photograph is too blurred to read"
    body["choices"][0]["message"]["content"] = json.dumps(content)

    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(short)
    with _client(vault, _ok(body)) as client:
        runner.drain(vault, Extractor(vault, client), queue)

    job = queue.for_artifact(short)
    assert job.state == jobs_mod.UNREADABLE
    assert job.state in jobs_mod.TERMINAL, "not retried; a person looks at it"
    assert "too blurred" in job.reason


def test_a_narrated_plan_is_an_unreadable_page_not_a_crash(vault):
    """The model talking instead of answering must not take the queue down."""
    short = _ingest(vault)
    body = {
        "id": "x",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Let me start by looking at the header of this script.",
                },
            }
        ],
    }
    with _client(vault, _ok(body)) as client:
        outcome = Extractor(vault, client).run(short)

    assert outcome.state == jobs_mod.UNREADABLE
    assert outcome.claims == 0
    completed = next(e for e in outcome.events if e.type == propose.EXTRACTION_COMPLETED)
    assert "Let me start by" in completed.payload["raw_output"], "still recorded verbatim"


def test_an_artefact_with_no_bytes_is_a_missing_file_not_a_lost_record(vault):
    short = _ingest(vault)
    stored = next(
        vault.raw.raw_dir.rglob(f"*{short}*.jpg")
    )
    stored.chmod(0o600)
    stored.unlink()

    with _client(vault, _ok()) as client:
        outcome = Extractor(vault, client).run(short)

    assert outcome.state == jobs_mod.NEEDS_ATTENTION
    assert "missing file rather than a lost record" in outcome.reason


# --- the queue file itself -------------------------------------------------


def test_a_torn_final_line_is_reported_not_rewritten(vault):
    """A crash mid-write. The same discipline as the event log."""
    agent_dir = vault.root / ".agent"
    queue = jobs_mod.Queue.open(agent_dir)
    queue.add("a3f91c")
    with queue.path.open("ab") as handle:
        handle.write(b'{"id": "job-torn", "artifa')

    reopened = jobs_mod.Queue.open(agent_dir)

    assert len(reopened.malformed) == 1
    assert reopened.for_artifact("a3f91c") is not None, "the intact job still reads"

    # And the next append lands cleanly rather than joining the torn bytes.
    reopened.add("77b210")
    third = jobs_mod.Queue.open(agent_dir)
    assert third.for_artifact("77b210").state == jobs_mod.QUEUED
    assert len(third.malformed) == 1


def test_state_is_the_fold_of_the_lines_not_the_last_one(vault):
    agent_dir = vault.root / ".agent"
    queue = jobs_mod.Queue.open(agent_dir)
    job = queue.add("a3f91c")
    queue.started(job)

    lines = [
        json.loads(line)
        for line in queue.path.read_text().splitlines()
        if line.strip()
    ]
    assert len(lines) == 2, "every transition appends; nothing is rewritten"
    assert jobs_mod.Queue.open(agent_dir).for_artifact("a3f91c").attempts == 1


def test_queueing_the_same_artefact_twice_does_not_queue_it_twice(vault):
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    first = queue.add("a3f91c")
    second = queue.add("a3f91c")

    assert first.id == second.id


def test_enqueue_unread_skips_what_the_log_has_already_read(vault):
    first = _ingest(vault)
    second = _ingest(vault, _script_image(("ATORVASTATIN 20mg",)))

    with _client(vault, _ok()) as client:
        for event in Extractor(vault, client).run(first).events:
            vault.append(event)

    queue = jobs_mod.Queue.open(vault.root / ".agent")
    added = runner.enqueue_unread(vault, queue)

    assert [job.artifact for job in added] == [second]
