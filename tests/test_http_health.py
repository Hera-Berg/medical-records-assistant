"""``/api/health`` keeps the three endpoint states apart, and leaks no key.

Two failures this guards against, both of which look fine on the screen that
produced them.

**Collapsing unreachable into unauthorised.** ``MODELS.md``: "Collapsing them
into one 'offline' state means a rotated key looks like a sleeping Mac and
nobody investigates for a week." One state is a Mac that will wake up and drain
the queue by itself; the other needs a person to set a key. So the assertions
below check that the two are distinguishable from the response alone, not merely
that both are reported as *something*.

**Reporting a credential.** ``MODELS.md`` is explicit that ``/api/health``
reports ``auth: ok | failed | missing`` "and nothing more", and that no route
returns the key or any prefix of it. The test walks the whole serialised
response for the value and for every prefix of it long enough to be worth
having.
"""

from __future__ import annotations

import json

import pytest

from agent.extract import jobs as jobs_mod
from agent.extract.probe import ProbeReport
from agent.llm import redaction
from agent.server import endpoint_state

from .conftest import claim, confirm, ingested, on_day

KEY = "sk-not-a-real-key-2f8a11c0"


@pytest.fixture(autouse=True)
def forget_secrets():
    yield
    redaction.forget_all()


def test_health_reports_a_vault_with_no_endpoint_as_not_configured(client):
    """A vault with no ``[models.vlm]`` is not a broken one.

    A demo vault is deliberately one of these. Capture works and the record
    serves; saying "unreachable" would send someone to check a box that was
    never configured.
    """
    body = client.get("/api/health").json()
    assert body["endpoint"]["state"] == endpoint_state.NOT_CONFIGURED
    assert body["endpoint"]["auth"] == "missing"
    assert "no inference endpoint configured" in body["endpoint"]["message"]
    # Not a problem: nothing is wrong and nothing needs doing.
    assert body["problems"] == []
    assert body["ok"] is True


def test_unreachable_and_unauthorised_are_distinguishable(app, client):
    """The two states differ in the response, not only in a log line."""
    state = app.state.record

    state.record_endpoint_error(_error("EndpointUnreachable"))
    asleep = client.get("/api/health").json()

    state.record_endpoint_error(_error("AuthRejected"))
    rejected = client.get("/api/health").json()

    assert asleep["endpoint"]["state"] == endpoint_state.UNREACHABLE
    assert rejected["endpoint"]["state"] == endpoint_state.UNAUTHORISED
    assert asleep["endpoint"]["state"] != rejected["endpoint"]["state"]
    assert asleep["endpoint"]["auth"] == "ok"
    assert rejected["endpoint"]["auth"] == "failed"

    # And they differ in what they ask of the user: one is a problem, one is not.
    assert asleep["problems"] == []
    assert any("rotated" in problem for problem in rejected["problems"])
    assert asleep["ok"] is True
    assert rejected["ok"] is False


def test_auth_is_only_ever_one_of_three_words(app, client):
    """``ok | failed | missing`` and nothing more — no source, no length."""
    state = app.state.record
    for exc_name in ("EndpointUnreachable", "AuthRejected", "CredentialError"):
        state.record_endpoint_error(_error(exc_name))
        auth = client.get("/api/health").json()["endpoint"]["auth"]
        assert auth in {"ok", "failed", "missing"}


def test_health_never_carries_the_key_or_a_prefix_of_it(app, client):
    """The frontend has no read path to the credential. Not even a fragment.

    A prefix is enough to shoulder-surf and enough to correlate against a
    keychain entry, so the assertion covers prefixes rather than only the whole
    value.
    """
    redaction.register(KEY)
    app.state.record.record_endpoint_error(_error("AuthRejected"))

    body = json.dumps(client.get("/api/health").json())
    assert KEY not in body
    for length in range(8, len(KEY) + 1):
        assert KEY[:length] not in body
    redaction.assert_absent(body, "/api/health", secrets=[KEY])


def test_probe_report_becomes_a_state_without_carrying_its_prose(app, client):
    """A probe's detail is for the terminal; the API gets a code and a sentence.

    The probe writes prose about what failed, and prose is what may not cross
    this boundary — it is assembled near the client and can quote a request.
    """
    report = ProbeReport(
        state="unauthorised",
        checks=(),
        auth="failed",
        model_reported=None,
        notes=("something the probe wanted to say",),
    )
    app.state.record.record_probe(report)

    endpoint = client.get("/api/health").json()["endpoint"]
    assert endpoint["state"] == endpoint_state.UNAUTHORISED
    assert endpoint["reason"] == "key-rejected"
    assert endpoint["message"] == endpoint_state.MESSAGES["key-rejected"]
    assert "something the probe wanted to say" not in json.dumps(endpoint)


def test_health_reports_queue_depth_and_parked_jobs(vault, client):
    """Depth is what is waiting; parked is what needs a person."""
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add("a3f91c")
    queue.add("77b210")

    body = client.get("/api/health").json()
    assert body["queue"]["depth"] == 2
    assert body["queue"]["parked"] is False

    queue.park_for_auth("the key was rejected")
    body = client.get("/api/health").json()
    assert body["queue"]["blocked_auth"] == 2
    assert body["queue"]["parked"] is True
    assert body["ok"] is False


def test_health_counts_anomalies_so_they_cannot_scroll_past(vault, client):
    """Anomalies are regenerable and unpersisted, so they must be surfaced.

    ``CLAUDE.md``: "``/api/health`` in phase 5 and the review inbox in phase 7
    both report the count, so they cannot scroll past unseen."
    """
    # A payload whose declared consequence disagrees with the computed one. The
    # code decides and the disagreement is reported — that is what makes the
    # gate hold against a buggy extractor rather than a cooperative one.
    proposal = claim(
        vault.identity.id,
        "med:perindopril",
        "dose",
        "5mg daily",
        ts=on_day(2),
    )
    proposal.payload["consequence"] = "low"
    vault.append(ingested(vault.identity.id, "a3f91c", ts=on_day(1)))
    vault.append(proposal)
    vault.append(confirm(vault.identity.id, proposal.id, ts=on_day(3)))

    body = client.get("/api/health").json()
    assert body["anomalies"]["count"] >= 1
    assert any("consequence" in note for note in body["anomalies"]["items"])


def test_health_reports_a_device_that_may_not_append(app, client, monkeypatch):
    """A cloned identity file stops appends but not reads.

    The server keeps serving and says so here, rather than letting the next
    capture be the thing that discovers it.
    """
    from agent import device as device_mod

    monkeypatch.setattr(
        device_mod.DeviceIdentity,
        "mismatch_reason",
        lambda self: "this identity was issued on another machine",
    )
    device = client.get("/api/health").json()["vault"]["device"]
    assert device["appendable"] is False
    assert "another machine" in device["reason"]


def test_a_vault_that_cannot_accept_a_capture_is_not_ok(app, client, monkeypatch):
    """A writable directory is not the same as a vault that accepts an append.

    Found by reading the response against a real vault: the device identity was
    missing, every capture failed at the append, and health reported ``ok`` with
    an empty problems list. Discovering it by failing a capture is discovering it
    in the waiting room, which is the moment this route exists to spare.
    """
    from agent import device as device_mod

    monkeypatch.setattr(
        device_mod.DeviceIdentity,
        "mismatch_reason",
        lambda self: "this identity was issued on another machine",
    )
    body = client.get("/api/health").json()

    assert body["vault"]["writable"] is True, "the directory itself is fine"
    assert body["vault"]["device"]["appendable"] is False
    assert body["ok"] is False
    assert any("another machine" in problem for problem in body["problems"])


def test_health_does_not_open_a_socket(app, client):
    """A polled route must not wait out a connect timeout against a sleeping box.

    Reachability is the worker's to learn. If this route contacted the endpoint,
    every poll would block for the connect timeout and the interface would stop
    repainting whenever the box was down — which is exactly when it is most
    needed.
    """
    import agent.extract.session as session_mod

    def explode(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("/api/health opened a client to the inference box")

    app.state.record.set_endpoint(
        endpoint_state.EndpointState(state=endpoint_state.UNREACHABLE, reason="asleep")
    )
    original = session_mod.open_client
    session_mod.open_client = explode
    try:
        assert client.get("/api/health").status_code == 200
    finally:
        session_mod.open_client = original


def _error(name: str) -> Exception:
    from agent import errors

    return getattr(errors, name)("a message the API must not repeat")
