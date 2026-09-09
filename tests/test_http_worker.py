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
