"""The background worker, driven synchronously.

Every test here calls :meth:`agent.server.worker.Worker.run_once` directly
rather than starting the thread and waiting. That is the point of ``run_once``
being a method that returns: a test which passes because a daemon thread
happened to be scheduled first is a test that fails on a slower machine for
reasons nobody can reproduce, so the loop is exercised one iteration at a time
with no timing in it at all.

The behaviour under test is the distinction ``MODELS.md`` insists on. A ``401``
is terminal — "Do not retry with backoff. Retrying a rotated key fifty times
achieves nothing and may trip rate limiting or lockout on the server" — so the
queue parks and the loop stops asking. An unreachable box is the expected
condition, so it backs off and the queue drains by itself later.
"""

from __future__ import annotations

import pytest

from agent.errors import (
    AuthRejected,
    EndpointNotConfigured,
    EndpointUnreachable,
    ModelIdentityMismatch,
)
from agent.extract import jobs as jobs_mod
from agent.server import endpoint_state
from agent.server.state import RecordState, under_pytest
from agent.server.worker import PROBE_SECONDS, UNREACHABLE_SECONDS, Worker

from .conftest import JPEG, api_client


def test_the_worker_is_off_by_default_under_pytest(vault):
    """No test may come to depend on background timing.

    The default is asserted rather than assumed, because a fixture that passes
    ``worker=False`` explicitly would hide a change to it.
    """
    assert under_pytest() is True
    from agent.server import create_app

    app = create_app(vault)
    assert app.state.worker is None


def test_the_worker_can_be_asked_for_explicitly(vault):
    from agent.server import create_app

    app = create_app(vault, worker=True)
    assert isinstance(app.state.worker, Worker)
    assert app.state.worker.running is False, "constructing one must not start it"


def test_a_vault_with_no_endpoint_leaves_the_worker_idle_and_says_so(vault):
    """Not a fault. A demo vault is deliberately one of these."""
    state = RecordState(vault)
    worker = Worker(state)

    wait = worker.run_once()

    assert state.endpoint.state == endpoint_state.NOT_CONFIGURED
    assert wait == PROBE_SECONDS


def test_an_unreachable_box_backs_off_and_is_not_a_problem(vault, monkeypatch):
    """The Mac is asleep. Retry silently, drain later."""
    _endpoint_raises(monkeypatch, EndpointUnreachable("connection refused"))
    state = RecordState(vault)
    worker = Worker(state)

    wait = worker.run_once()

    assert state.endpoint.state == endpoint_state.UNREACHABLE
    assert state.endpoint.is_terminal is False
    assert wait == UNREACHABLE_SECONDS
    # And it keeps trying, because that is what "drain later" means.
    assert worker.run_once() == UNREACHABLE_SECONDS


def test_a_rejected_key_stops_the_worker_asking(vault, monkeypatch):
    """Terminal. Retrying a rotated key fifty times may trip lockout."""
    calls = _endpoint_raises(monkeypatch, AuthRejected("401"))
    state = RecordState(vault)
    worker = Worker(state)

    worker.run_once()
    assert state.endpoint.state == endpoint_state.UNAUTHORISED
    assert state.endpoint.auth == "failed"

    before = len(calls)
    worker.run_once()
    worker.run_once()
    assert len(calls) == before, "the worker kept asking after a 401"


def test_resuming_after_a_new_key_puts_the_jobs_back(vault, monkeypatch):
    _endpoint_raises(monkeypatch, AuthRejected("401"))
    state = RecordState(vault)
    queue = state.queue()
    queue.add("a3f91c")
    queue.add("77b210")
    queue.park_for_auth("the key was rejected")

    worker = Worker(state)
    worker.run_once()

    assert worker.resume() == 2
    assert state.queue().is_parked is False
    assert state.queue().depth() == 2


def test_a_model_mismatch_is_misconfiguration_not_unreachability(vault, monkeypatch):
    """"Which model produced this claim" has to stay answerable a year later."""
    _endpoint_raises(monkeypatch, ModelIdentityMismatch("the box reports something else"))
    state = RecordState(vault)
    Worker(state).run_once()

    assert state.endpoint.state == endpoint_state.MISCONFIGURED
    assert state.endpoint.reason == "model-mismatch"


def test_the_error_message_never_becomes_the_reported_state(vault, monkeypatch):
    """The class decides. The message is what this codebase does not control."""
    _endpoint_raises(
        monkeypatch, AuthRejected("Authorization: Bearer sk-leaky-value-here")
    )
    state = RecordState(vault)
    Worker(state).run_once()

    reported = state.endpoint.to_dict()
    assert "sk-leaky" not in str(reported)
    assert reported["message"] == endpoint_state.MESSAGES["key-rejected"]


def test_capture_nudges_the_worker_without_waiting_for_it(vault, monkeypatch):
    """A nudge is a nudge. The response does not depend on it landing."""
    _endpoint_raises(monkeypatch, EndpointUnreachable("asleep"))

    with api_client(vault, worker=True) as client:
        worker = client.app.state.worker
        worker._wake.clear()
        response = client.post(
            "/api/capture", files={"files": ("a.jpg", JPEG, "image/jpeg")}
        )

    assert response.status_code == 202
    # The bytes and the job are on disk regardless of what the thread did.
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    assert queue.depth() == 1


def test_an_exception_in_one_pass_does_not_kill_the_loop(vault, monkeypatch):
    """A worker that dies quietly is worse than one that logs and carries on."""
    import agent.extract.session as session_mod

    def explode(*args, **kwargs):
        raise RuntimeError("something nobody anticipated")

    monkeypatch.setattr(session_mod, "open_client", explode)
    state = RecordState(vault)
    worker = Worker(state)

    with pytest.raises(RuntimeError):
        worker.run_once()

    # The loop wraps it; `run_once` is deliberately honest to its caller.
    worker._stop.set()
    worker._loop()  # returns rather than propagating


def _endpoint_raises(monkeypatch, exc):
    """Make every attempt to open a client raise *exc*, and count the attempts."""
    import agent.extract.session as session_mod

    calls: list[int] = []

    def refuse(*args, **kwargs):
        calls.append(1)
        raise exc

    monkeypatch.setattr(session_mod, "open_client", refuse)
    return calls


# --- speech drains without the box -------------------------------------------
#
# The reason the loop is shaped the way it is. Until phase 6 every drain
# happened inside the client context, so an unconfigured endpoint, a sleeping
# box or a queue parked on a rejected key all stopped a *recording* being typed
# up — by a model that runs on this machine and needs none of them. Each of the
# three is asserted separately, because each one used to break it on its own.


def _recorded(vault):
    """One recording in the vault, queued, with its bytes in raw/."""
    from agent import ingest as ingest_mod

    from .test_speech import _wav_bytes

    return ingest_mod.ingest_bytes(
        vault,
        _wav_bytes(),
        ingest_mod.CaptureContext(source="recorder", captured_ts="2026-09-02T09:11:00Z"),
    )


def _worker_with_speech(state, text="The headaches have been better."):
    from .test_speech import _speaking

    return Worker(state, speech=_speaking(text))


def _transcripts(vault):
    return [
        event.payload["transcript"]
        for event in vault.read().events
        if event.type == "extraction.completed" and "transcript" in event.payload
    ]


def test_a_recording_is_typed_up_with_no_endpoint_configured(vault):
    recorded = _recorded(vault)
    state = RecordState(vault)
    worker = _worker_with_speech(state)

    worker.run_once()

    assert _transcripts(vault) == ["The headaches have been better."]
    # Typed up locally, and queued again for the reader that turns those words
    # into claims. That second stage needs the box, which this vault has none
    # of — so it waits, and the recording is safe and readable meanwhile.
    assert state.queue().for_artifact(recorded.short).state == jobs_mod.QUEUED
    # And the endpoint is still reported honestly: nothing about speech running
    # says anything about the box.
    assert state.endpoint.state == endpoint_state.NOT_CONFIGURED


def test_a_recording_is_typed_up_while_the_box_is_asleep(vault, monkeypatch):
    _endpoint_raises(monkeypatch, EndpointUnreachable("connection refused"))
    _recorded(vault)
    state = RecordState(vault)

    _worker_with_speech(state).run_once()

    assert _transcripts(vault) == ["The headaches have been better."]
    assert state.endpoint.state == endpoint_state.UNREACHABLE


def test_a_recording_is_typed_up_while_the_queue_is_parked_on_a_rejected_key(
    vault, monkeypatch
):
    """The case that matters most: a parked queue is a person-shaped problem,
    and it has nothing to do with a voice note and a local model."""
    from agent import ingest as ingest_mod

    _endpoint_raises(monkeypatch, AuthRejected("401 from the box"))
    # A photograph, so the queue has something for the *vision* reader to park.
    ingest_mod.ingest_bytes(
        vault,
        b"\xff\xd8\xff\xe0 not really a photograph",
        ingest_mod.CaptureContext(source="upload"),
    )
    state = RecordState(vault)
    worker = _worker_with_speech(state)

    worker.run_once()
    assert state.queue().is_parked

    # Now a voice note, recorded after the key was rejected. The queue is parked
    # and the box is unreachable to this process; neither is between a
    # microphone and a transcript.
    _recorded(vault)
    worker.run_once()

    assert _transcripts(vault) == ["The headaches have been better."]
    assert state.queue().is_parked, "the vision half stays parked"


def test_a_broken_speech_path_does_not_stop_the_vision_pass(vault, monkeypatch):
    """One reader failing must not take the other down with it."""
    from agent.errors import ProjectionError

    def explode(*args, **kwargs):
        raise ProjectionError("the speech pass is broken")

    monkeypatch.setattr("agent.server.worker.speech_mod.drain", explode)
    _endpoint_raises(monkeypatch, EndpointUnreachable("connection refused"))
    state = RecordState(vault)

    wait = Worker(state).run_once()

    assert state.endpoint.state == endpoint_state.UNREACHABLE
    assert wait == UNREACHABLE_SECONDS


def test_parking_the_queue_never_marks_a_recording_blocked_on_authentication(
    vault, monkeypatch
):
    """A rejected key has nothing to do with a model running on this laptop.

    Marking a recording ``blocked-auth`` would be false, and it would hide the
    voice note behind a resume the user has no reason to perform — which is the
    whole promise of local speech, broken by a bookkeeping shortcut.
    """
    from agent import ingest as ingest_mod

    _endpoint_raises(monkeypatch, AuthRejected("401 from the box"))
    photo = ingest_mod.ingest_bytes(
        vault,
        b"\xff\xd8\xff\xe0 not really a photograph",
        ingest_mod.CaptureContext(source="upload"),
    )
    recording = _recorded(vault)
    state = RecordState(vault)

    # A worker with no speech model at all: the recording is not transcribed
    # here, so it is still waiting when the park happens.
    class Absent:
        def transcribe(self, path, **options):
            from agent.asr.audio import DecodeUnavailable

            raise DecodeUnavailable("faster-whisper is not installed")

    Worker(state, speech=Absent()).run_once()

    queue = state.queue()
    assert queue.for_artifact(photo.short).state == jobs_mod.BLOCKED_AUTH
    assert queue.for_artifact(recording.short).state != jobs_mod.BLOCKED_AUTH
