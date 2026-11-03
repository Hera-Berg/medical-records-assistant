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
