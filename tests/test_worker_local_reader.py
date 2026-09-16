"""The worker, health and capture with documents read on this computer.

The reader is the fake llama-server from :mod:`tests.fixtures`, run as a real
child. The worker is driven one pass at a time, as in ``test_http_worker.py``.
"""

from __future__ import annotations

import io
import sys
import threading

import pytest
from PIL import Image

from agent import ingest as ingest_mod
from agent.extract import jobs as jobs_mod
from agent.extract import propose
from agent.extract import runner as runner_mod
from agent.runtime import choice, manifest, states
from agent.runtime.store import Store
from agent.server import endpoint_state
from agent.server.state import RecordState
from agent.server.worker import Worker

from .conftest import JPEG, api_client
from .test_reader_supervisor import make_reader

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process handling")


@pytest.fixture
def ready_store(isolated_reader, monkeypatch):
    store = Store(root=isolated_reader)
    monkeypatch.setattr(Store, "is_ready", lambda self, bundles: True)
    monkeypatch.setattr(Store, "prepare", lambda self, bundles: None)
    monkeypatch.setattr(Store, "missing", lambda self, bundles: [])
    return store


def _photo(vault) -> str:
    image = Image.new("RGB", (400, 300), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = ingest_mod.ingest_bytes(vault, buffer.getvalue(), ingest_mod.CaptureContext(source="upload"))
    return result.short


class NoSpeech:
    def transcribe(self, path, **options):  # pragma: no cover - never called
        raise AssertionError("no recordings in these tests")


def test_a_new_vault_reads_on_this_computer_and_says_the_files_are_not_here(vault):
    state = RecordState(vault)
    Worker(state, speech=NoSpeech()).run_once()
    reported = state.endpoint.to_dict()
    assert reported["state"] == endpoint_state.NOT_DOWNLOADED
    assert reported["where"] == endpoint_state.THIS_COMPUTER
    assert reported["auth"] == "ok", "there is no key for a person to go looking for"
    assert reported["message"] == states.MESSAGES["not-downloaded"]


def test_health_does_not_call_a_missing_download_a_problem(vault):
    with api_client(vault) as client:
        body = client.get("/api/health").json()
    assert body["endpoint"]["state"] == endpoint_state.NOT_DOWNLOADED
    assert body["problems"] == []


def test_capture_works_before_anything_is_downloaded(vault):
    with api_client(vault) as client:
        response = client.post("/api/capture", files={"files": ("a.jpg", JPEG, "image/jpeg")})
    assert response.status_code in (200, 202)
    assert RecordState(vault).queue().depth() == 1


def test_a_demo_vault_reads_on_this_computer_like_any_other(vault, monkeypatch):
    monkeypatch.setattr(type(vault), "is_demo", property(lambda self: True))
    from agent.extract import session

    assert session.reads_here(vault) is True


def test_a_demo_vault_still_says_it_is_a_demonstration(vault, monkeypatch):
    monkeypatch.setattr(type(vault), "is_demo", property(lambda self: True))
    with api_client(vault) as client:
        health = client.get("/api/health").json()
        reader = client.get("/api/reader").json()
    assert health["vault"]["demo"] is True
    assert reader["demo"] is True
    assert reader["reader"] is not None, "a demo reports its reader like any vault"


def test_the_first_pass_starts_the_reader_and_probes_it(vault, ready_store):
    reader = make_reader(ready_store)
    state = RecordState(vault)
    Worker(state, speech=NoSpeech()).run_once()
    reported = state.endpoint.to_dict()
    assert reported["state"] == endpoint_state.WORKING
    assert reported["reason"] == "ready"
    assert reported["where"] == endpoint_state.THIS_COMPUTER
    assert reported["model"] == manifest.ALIAS
    assert reader.status().state == states.READY


def test_a_read_records_what_read_it_as_facts(vault, ready_store):
    make_reader(ready_store)
    short = _photo(vault)
    state = RecordState(vault)
    worker = Worker(state, speech=NoSpeech())
    worker.run_once()
    worker.run_once()

    events = list(vault.read().events)
    (completed,) = [e for e in events if e.type == propose.EXTRACTION_COMPLETED and e.payload["artifact"] == short]
    runtime = completed.payload["runtime"]
    assert runtime["kind"] == "bundled"
    assert runtime["engine"] == manifest.ENGINE
    assert runtime["files"][manifest.WEIGHTS.name] == f"sha256:{manifest.WEIGHTS.sha256}"
    assert runtime["elapsed_s"] >= 0
    assert completed.provenance["runtime_kind"] == "bundled"
    assert completed.device == vault.identity.id
    assert "degraded" not in repr(completed.payload).lower()
    # "This computer" is false on every other machine that syncs the folder.
    assert "this-computer" not in repr(completed.to_dict())


def test_a_sleeping_reader_is_not_woken_when_there_is_nothing_to_read(vault, ready_store):
    reader = make_reader(ready_store)
    state = RecordState(vault)
    worker = Worker(state, speech=NoSpeech())
    worker.run_once()
    reader.stop()
    assert reader.status().state == states.SLEEPING

    worker.run_once()
    assert reader.status().state == states.SLEEPING
    assert state.endpoint.state == endpoint_state.SLEEPING
    assert state.endpoint.to_dict()["message"].startswith("Sleeping to free memory")


def test_a_capture_wakes_it(vault, ready_store):
    reader = make_reader(ready_store)
    state = RecordState(vault)
    worker = Worker(state, speech=NoSpeech())
    worker.run_once()
    reader.stop()

    _photo(vault)
    worker.run_once()
    assert reader.status().state == states.READY
    assert state.queue().depth() == 0


def test_a_waiting_question_goes_before_the_next_document(vault, ready_store):
    reader = make_reader(ready_store)
    state = RecordState(vault)
    worker = Worker(state, speech=NoSpeech())
    worker.run_once()
    short = _photo(vault)

    with reader.question():
        worker.run_once()
        assert state.queue().for_artifact(short).state == jobs_mod.QUEUED
    worker.run_once()
    assert state.queue().for_artifact(short).state != jobs_mod.QUEUED


def test_a_stopped_reader_is_reported_and_retry_brings_it_back(vault, ready_store, monkeypatch):
    monkeypatch.setenv("FAKE_VISION", "0")
    reader = make_reader(ready_store)
    state = RecordState(vault)
    worker = Worker(state, speech=NoSpeech())
    worker.run_once()
    assert state.endpoint.state == endpoint_state.STOPPED
    assert state.endpoint.reason == "no-vision"
    assert state.endpoint.is_terminal

    monkeypatch.delenv("FAKE_VISION")
    reader.retry()
    worker.reconfigured()
    worker.run_once()
    assert state.endpoint.state == endpoint_state.WORKING


def test_the_record_lock_is_not_held_while_a_document_is_read(vault):
    """A capture appends under this lock and must return before inference runs."""
    lock = threading.RLock()
    held_during_read: list[bool] = []

    class Extractor:
        def run(self, short):
            def try_it():
                got = lock.acquire(timeout=0.5)
                held_during_read.append(not got)
                if got:
                    lock.release()

            probe = threading.Thread(target=try_it)
            probe.start()
            probe.join()
            return runner_mod.Outcome(short, jobs_mod.DONE)

    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add("a3f91c")
    report = runner_mod.drain(vault, Extractor(), queue, lock=lambda: lock)
    assert report.outcomes
    assert held_during_read == [False]


def test_the_choice_is_per_machine_and_never_in_config_toml(vault, ready_store):
    choice.save(choice.THIS_COMPUTER)
    text = (vault.root / "config.toml").read_text()
    assert "this-computer" not in text and "reads_on" not in text
    assert not choice.path().is_relative_to(vault.root)


def _claim_read_by(provenance):
    from dataclasses import replace as dc_replace

    from agent.projection import claims as claims_mod
    from agent.server import serialise

    from .conftest import claim as authored

    event = authored("elbook-yar0", "med:perindopril", "dose", "5mg daily")
    event = dc_replace(event, provenance=provenance)
    parsed = claims_mod.parse(event)
    return serialise.read_by(parsed)


def test_a_claim_says_what_read_it_on_which_device():
    facts = _claim_read_by(
        {"model": manifest.ALIAS, "model_rev": manifest.ALIAS, "runtime_kind": "bundled", "artifact": "a3f91c"}
    )
    assert facts == {
        "model": manifest.ALIAS,
        "runtime": "bundled",
        "device": "elbook-yar0",
        "sentence": "Read by Qwen3.5-4B Q4_K_M running on elbook-yar0.",
    }


def test_a_claim_read_elsewhere_or_before_phase_11_says_only_what_is_recorded():
    remote = _claim_read_by({"model": "Qwen3.8-Flash-Next", "runtime_kind": "endpoint", "artifact": "a3f91c"})
    assert remote["sentence"] == "Read by Qwen3.8-Flash-Next on a computer connected from elbook-yar0."
    older = _claim_read_by({"model": "qwen3.5:9b", "artifact": "a3f91c"})
    assert older["sentence"] == "Read by qwen3.5:9b."
    for facts in (remote, older):
        assert "degraded" not in facts["sentence"].lower()
