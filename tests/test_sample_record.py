"""The demo record beside the person's own: never mixed, whichever tab asks.

The guarantee that matters is the one stated at its widest: a request from a tab
showing the person's own record never reads the demo record — Ask included — and
the reverse, whatever the browser's cookie says. These tests drive the router the
served app is wrapped in, not the two apps separately.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent.events import envelope
from agent.server import sample_record
from agent.server.app import create_app

from .conftest import LOOPBACK_BASE

ORIGIN = {"origin": LOOPBACK_BASE}
OWN = {"x-health-record": "own"}
DEMO = {"x-health-record": "demo"}
#: A word only the person's own record holds, and words only the demo holds.
OWN_ONLY = "zebrafish"
DEMO_ONLY = ("apixaban", "Jane", "Thompson", "sodium")


@pytest.fixture
def served(vault):
    vault.append(envelope.new("note.recorded", vault.identity.id,
                              payload={"text": f"I have started taking {OWN_ONLY} oil every morning."}))
    app = sample_record.wrap(create_app(vault, worker=False), vault)
    with TestClient(app, base_url=LOOPBACK_BASE) as client:
        yield client


def enter(client, workspace="patient"):
    response = client.post("/api/demo/enter", json={"workspace": workspace}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    return response.json()


def ask(client, question, headers):
    response = client.post("/api/ask", json={"question": question}, headers={**ORIGIN, **headers})
    assert response.status_code == 200, response.text
    answer = response.json()
    answer.pop("question")  # echoed back as asked; everything else is the record's
    return json.dumps(answer)


def test_the_demo_record_ships_with_the_program():
    assert sample_record.available()


def test_status_says_own_until_the_demo_is_entered(served):
    assert served.get("/api/demo").json() == {"record": "own", "available": True}
    enter(served)
    assert served.get("/api/demo").json()["record"] == "demo"
    served.post("/api/demo/leave", headers=ORIGIN)
    assert served.get("/api/demo").json()["record"] == "own"


def test_a_tab_on_the_own_record_never_reads_the_demo_even_after_another_tab_switched(served):
    enter(served)  # the browser's cookie now says demo, as another tab left it
    for question in ("What medicines am I taking?", "What did Dr Thompson say?", "What was my sodium?"):
        answer = ask(served, question, OWN)
        for word in DEMO_ONLY:
            assert word.lower() not in answer.lower(), (question, word)
    for path in ("/api/timeline", "/api/wiki", "/api/health"):
        body = served.get(path, headers=OWN).text
        for word in DEMO_ONLY:
            assert word.lower() not in body.lower(), (path, word)


def test_a_tab_on_the_demo_never_reads_the_own_record(served):
    enter(served)
    assert OWN_ONLY in served.get("/api/timeline", headers=OWN).text
    for path in ("/api/timeline", "/api/wiki", "/api/patient/documents"):
        assert OWN_ONLY not in served.get(path, headers=DEMO).text, path
    assert OWN_ONLY not in ask(served, f"When did I start {OWN_ONLY} oil?", DEMO)


def test_the_demo_answers_from_its_own_record(served):
    enter(served)
    answer = ask(served, "What medicines am I taking?", DEMO)
    assert "Apixaban" in answer or "apixaban" in answer


def test_a_header_naming_neither_record_is_refused(served):
    assert served.get("/api/health", headers={"x-health-record": "both"}).status_code == 400


def test_without_header_or_cookie_a_request_reaches_the_own_record(served):
    assert served.get("/api/health").json()["vault"]["demo"] is False
    enter(served)
    # A navigation (no header) follows the cookie; that is what opening a page needs.
    assert served.get("/api/health").json()["vault"]["demo"] is True


def test_demo_clinician_sign_in_never_replaces_the_own_records_session(served):
    assert enter(served, "clinician")["path"] == "/clinician"
    assert "care_sample_clinician_session" in served.cookies
    assert "care_clinician_session" not in served.cookies
    dashboard = served.get("/api/clinician/dashboard", headers=DEMO)
    assert dashboard.status_code == 200
    assert dashboard.json()["tab_counts"]["action_required"] == 1
    # The own record has no such session and no accounts.
    assert served.get("/api/clinician/dashboard", headers=OWN).status_code == 401


def test_the_demo_is_never_placed_inside_the_own_record(vault):
    with pytest.raises(Exception, match="refusing"):
        sample_record.materialise(vault.root, vault.root / "sample")
    with pytest.raises(Exception, match="refusing"):
        sample_record.materialise(vault.root, vault.root.parent)


def test_the_demo_reads_without_demonstration_disclaimers(served):
    enter(served)
    for path in ("/api/timeline", "/api/calendar", "/api/patient/documents"):
        body = served.get(path, headers=DEMO).text
        assert "Fictional demo" not in body, path
        assert "fictional demonstration" not in body.lower(), path


def test_the_demo_clock_is_the_moment_the_record_describes(served):
    enter(served)
    upcoming = served.get("/api/calendar", headers=DEMO).json()
    dates = {row["date"] for row in (upcoming.get("events") if isinstance(upcoming, dict) else upcoming)}
    assert {"2026-11-05", "2026-11-12", "2026-11-18"} <= dates


# ---- the demo reads with the own record's connection ------------------------
#
# The demo's settings file has no [models.vlm] table and is rebuilt at every
# start, so a connection could never be made on it and last. It reads with the
# computer the person's own record is connected to — read live, so a Connect on
# the own record reaches the demo without a restart — and cannot change it.

OWN_BOX = "http://100.64.0.5/v1"


def connect_own(client, base_url=OWN_BOX, model="own-box/model"):
    """Write the own record's connection, as a successful Connect would."""
    from agent import config as config_mod

    state = client.app.state.record
    config_mod.set_values(state.vault.root / "config.toml", {
        "models.vlm.base_url": base_url,
        "models.vlm.model": model,
    })
    state.reload_config()


def test_the_demo_settings_show_the_own_records_connection(served):
    connect_own(served)
    enter(served)
    demo = served.get("/api/settings", headers=DEMO).json()["endpoint"]
    own = served.get("/api/settings", headers=OWN).json()["endpoint"]
    assert demo["configured"] and demo["base_url"] == OWN_BOX
    assert demo["from_own_record"] and "own record" in demo["from_own_record"]
    assert own["from_own_record"] is None


def test_a_later_connect_on_the_own_record_reaches_the_demo_without_a_restart(served):
    connect_own(served)
    enter(served)
    served.get("/api/settings", headers=DEMO)  # the demo app now exists
    connect_own(served, base_url="http://100.64.0.9/v1", model="another/model")
    demo = served.get("/api/settings", headers=DEMO).json()["endpoint"]
    assert (demo["base_url"], demo["model"]) == ("http://100.64.0.9/v1", "another/model")


def test_the_demo_cannot_change_the_connection_or_the_key(served, vault):
    connect_own(served)
    enter(served)
    before = (vault.root / "config.toml").read_bytes()
    connect = served.post("/api/settings/endpoint/connect",
                          json={"base_url": "http://100.64.0.7/v1"}, headers={**ORIGIN, **DEMO})
    key = served.post("/api/settings/endpoint/key", json={"key": "demo-typed-key"},
                      headers={**ORIGIN, **DEMO})
    assert connect.status_code == 409 and key.status_code == 409
    assert "own record" in connect.json()["detail"]
    assert (vault.root / "config.toml").read_bytes() == before
    sample_config = sample_record.location() / "config.toml"
    import tomllib

    assert "vlm" not in tomllib.loads(sample_config.read_text(encoding="utf-8")).get("models", {})


def test_the_demo_answers_with_the_own_records_computer_from_the_demos_record(served, monkeypatch):
    from agent.extract import session

    from .conftest import FakeBox

    box = FakeBox(sentences=[])
    reached = []

    def open_client(vault, *args, **kwargs):
        reached.append(session.settings_for(vault).vlm.endpoint.base_url)
        return box

    monkeypatch.setattr(session, "open_client", open_client)
    connect_own(served)
    enter(served)
    answer = ask(served, "What medicines am I taking?", DEMO)
    assert reached == [OWN_BOX]
    assert box.calls, "the demo's question never reached the computer"
    sent = " ".join(call["text"] for call in box.calls)
    assert "apixaban" in sent.lower() and OWN_ONLY not in sent
    assert "No computer is set up" not in answer


def test_with_the_own_record_unconnected_the_demo_says_so(served):
    enter(served)
    demo = served.get("/api/settings", headers=DEMO).json()["endpoint"]
    assert demo["configured"] is False and demo["from_own_record"]
